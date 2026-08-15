import io, zipfile
from services.mail_attachment_processor import classify_attachment, _docx_text, _usable_text

def test_offer_attachment_classification():
    assert classify_attachment('Offer_Letter.pdf') == 'OFFER_LETTER'
    assert classify_attachment('salary_structure.pdf') == 'COMPENSATION_BREAKUP'

def test_docx_text_extraction():
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>Joining date confirmed</w:t></w:r></w:p></w:body></w:document>')
    assert 'Joining date confirmed' in _docx_text(output.getvalue())

def test_vision_fallback_text_quality_gate_rejects_ocr_garbage():
    assert _usable_text('Offer letter confirms employment and joining date.') is True
    assert _usable_text('|| | 1 . _') is False
