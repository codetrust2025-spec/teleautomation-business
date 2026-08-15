"""Checksum-cached extraction for supported recruitment email attachments."""
from __future__ import annotations
import base64, hashlib, io, os, re, shutil, subprocess, tempfile, zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from core import recruitment_mail_store as store
from core.ocr_policy import ocr_enabled

SUPPORTED={'.pdf','.doc','.docx','.txt','.ics','.png','.jpg','.jpeg','.webp'}

def _usable_text(text:str)->bool:
    """Reject empty or OCR-garbage text before semantic document analysis."""
    normalized=re.sub(r'\s+',' ',text or '').strip()
    alphanumeric=sum(char.isalnum() for char in normalized)
    letters=sum(char.isalpha() for char in normalized)
    return alphanumeric>=24 and letters/max(1,len(normalized))>=.35

def classify_attachment(filename:str,text:str='')->str:
    blob=re.sub(r'[_-]+',' ',f'{filename} {text[:12000]}'.lower())
    # Document identity is established before lifecycle analysis.  Specific
    # historical/sensitive documents intentionally precede generic words such
    # as "joining" because payslips commonly contain a Date of Joining field.
    rules=[
        ('PAYSLIP',r'\bpayslip\b|\bsalary slip\b|pay slip for the month'),
        ('EXPERIENCE_LETTER',r'experience letter|certificate of experience'),
        ('RELIEVING_LETTER',r'relieving letter|relieved from'),
        ('BANK_STATEMENT',r'bank statement'),
        ('RESUME',r'\bresume\b|curriculum vitae|\bcv\b'),
        ('ID_DOCUMENT',r'\baadhaar\b|\baadhar\b|\bpassport\b|\bpan card\b'),
        ('EDUCATION_DOCUMENT',r'degree certificate|marksheet|transcript'),
        ('OFFER_LETTER',r'offer[ _-]?letter|employment offer'),
        ('APPOINTMENT_LETTER',r'appointment[ _-]?letter'),
        ('COMPENSATION_BREAKUP',r'compensation|salary structure|ctc breakup'),
        ('INTERVIEW_INVITATION',r'interview|meeting invite'),
        ('ASSESSMENT_INSTRUCTIONS',r'assessment|coding test'),
        ('BACKGROUND_VERIFICATION_DOCUMENT',r'background verification|\bbgv\b'),
        ('DOCUMENT_VERIFICATION_REQUEST',r'document verification'),
        ('JOB_DESCRIPTION',r'job description|\bjd\b'),
        ('JOINING_LETTER',r'joining letter|joining confirmation|onboarding letter'),
    ]
    for label,pattern in rules:
        if re.search(pattern,blob):return label
    return 'OTHER_RECRUITMENT_DOCUMENT'

def _docx_text(data:bytes)->str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root=ElementTree.fromstring(archive.read('word/document.xml'))
    return ' '.join(node.text or '' for node in root.iter() if node.tag.endswith('}t'))

def _legacy_doc_text(data:bytes)->tuple[str,str]:
    """Extract old binary DOC files when a safe system converter is present."""
    with tempfile.TemporaryDirectory(prefix='ta-doc-') as directory:
        source=Path(directory)/'attachment.doc';source.write_bytes(data)
        antiword=shutil.which('antiword')
        if antiword:
            result=subprocess.run([antiword,str(source)],capture_output=True,timeout=30,check=False)
            if result.returncode==0:return result.stdout.decode('utf-8','replace'),'antiword'
        office=shutil.which('soffice') or shutil.which('libreoffice')
        if office:
            result=subprocess.run([office,'--headless','--convert-to','txt:Text','--outdir',directory,str(source)],capture_output=True,timeout=60,check=False)
            output=Path(directory)/'attachment.txt'
            if result.returncode==0 and output.exists():return output.read_text('utf-8',errors='replace'),'libreoffice'
    return '','legacy_doc_manual_review'

def _pdf_text(data:bytes)->str:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:return '\n'.join((p.extract_text() or '') for p in pdf.pages[:30])

def _image_text(data:bytes)->str:
    if not ocr_enabled():
        return ''
    from PIL import Image
    import pytesseract
    return pytesseract.image_to_string(Image.open(io.BytesIO(data)))

def _vision_summary(data:bytes,mime_type:str)->str:
    from core.ai_gateway import chat_structured,configured_models
    schema={"type":"object","required":["document_type","supported_text","confidence"],"properties":{"document_type":{"type":"string"},"supported_text":{"type":"string"},"confidence":{"type":"number","minimum":0,"maximum":1}},"additionalProperties":False}
    result=chat_structured(
        messages=[
            {"role":"system","content":"Read this recruitment document. Return only text visibly supported by the image. Do not invent salary, company, dates, or offer status."},
            {"role":"user","content":"Extract a short factual transcription and document type."},
        ],
        schema=schema,
        model=configured_models()['vision'],
        images=[base64.b64encode(data).decode()],
        workload="recruitment_attachment_vision",
    )
    import json
    parsed=json.loads(result.content);return str(parsed.get('supported_text') or '')

def _pdf_first_page_image(data:bytes)->bytes|None:
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            image=pdf.pages[0].to_image(resolution=130).original
        out=io.BytesIO();image.save(out,format='PNG');return out.getvalue()
    except Exception:return None

def extract_attachment(raw:dict[str,Any])->dict[str,Any]:
    data=raw.get('data') or b'';checksum=hashlib.sha256(data).hexdigest();suffix=Path(raw.get('filename') or '').suffix.lower();cached=store.attachment_cache(checksum)
    # Older releases cached .ics files as unsupported empty text.  Do not let
    # that stale entry permanently prevent deterministic calendar recovery.
    if cached and (suffix!='.ics' or 'BEGIN:VCALENDAR' in str(cached.get('extracted_text') or '').upper()):
        return {**raw,'checksum':checksum,'text':cached.get('extracted_text') or '','attachment_type':cached.get('attachment_type'),'extraction_method':cached.get('extraction_method'),'extraction_status':'CACHED'}
    text='';method='unsupported';status='UNSUPPORTED'
    try:
        if suffix in ('.txt','.ics'):text=data.decode('utf-8','replace');method='icalendar' if suffix=='.ics' else 'text'
        elif suffix=='.pdf':
            text=_pdf_text(data);method='pdfplumber'
            if not _usable_text(text):
                page_image=_pdf_first_page_image(data)
                if page_image:text=_vision_summary(page_image,'image/png');method='ollama_vision'
        elif suffix=='.docx':text=_docx_text(data);method='docx_xml'
        elif suffix in ('.png','.jpg','.jpeg','.webp'):
            text=_image_text(data);method='tesseract'
            if not _usable_text(text):text=_vision_summary(data,raw.get('mime_type') or 'image/jpeg');method='ollama_vision'
        elif suffix=='.doc':text,method=_legacy_doc_text(data)
        status='EXTRACTED' if text.strip() else 'MANUAL_REVIEW_REQUIRED'
    except Exception:status='FAILED'
    # RFC 5545 parsing depends on line/property boundaries and folded lines.
    # Preserve those for calendar files; normalize prose documents as before.
    text=(text.strip() if suffix=='.ics' else re.sub(r'\s+',' ',text).strip())[:100000]
    return {**raw,'data':None,'checksum':checksum,'text':text,'attachment_type':classify_attachment(raw.get('filename',''),text),'extraction_method':method,'extraction_status':status}
