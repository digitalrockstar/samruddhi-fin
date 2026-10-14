from app.services.matcher import normalise, parse_amount

def test_normalise_masks_variable_values():
    x=normalise("Paid Rs 1,234 on 12/09/2026 https://x.test/a/123456")
    assert "<num>" in x and "<date>" in x and "<url>" in x

def test_parse_amount():
    assert str(parse_amount("₹1,234.50")) == "1234.50"
    assert parse_amount("abc") is None
