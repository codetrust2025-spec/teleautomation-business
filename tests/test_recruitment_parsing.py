from services.recruitment_parsing import parse_ctc,parse_currency,parse_date,parse_time,sender_domain

def test_sender_domain():
    domain='test.invalid';assert sender_domain(f"Recruiter <jobs@{domain}>")==domain
def test_ctc_and_currency():
    assert parse_ctc('Annual CTC ₹14 LPA')==1400000
    assert parse_currency('Annual CTC ₹14 LPA')=='INR'
def test_dates():
    assert parse_date('12.07.2026')=='2026-07-12'
    assert parse_date('12 July 2026')=='2026-07-12'
def test_times():
    assert parse_time('1:30 PM')=='13:30:00'
    assert parse_time('16:00')=='16:00:00'
