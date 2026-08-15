import json
from pathlib import Path
from services.recruitment_mail_agent import relevance_score

def test_prefilter_evaluation_fixture():
    cases=json.loads((Path(__file__).parent/'fixtures'/'recruitment_email_cases.json').read_text())
    correct=0
    for case in cases:
        predicted=relevance_score(case['subject'],case['body'],case.get('filenames')) >= .55
        correct += predicted == case['relevant']
    assert correct/len(cases) >= .85
