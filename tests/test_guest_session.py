"""Guest session token issue/verify tests."""
from app.services.guest_session import issue_guest_token, verify_guest_token


def test_issued_token_verifies():
    token = issue_guest_token()
    assert token.startswith("guest_")
    guest_id = verify_guest_token(token)
    assert guest_id is not None
    import uuid
    uuid.UUID(guest_id)  # must be a valid UUID string


# def test_tampered_token_rejected():
#     token = issue_guest_token()
#     tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
#     assert verify_guest_token(tampered) is None

def test_tampered_token_rejected():
    token = issue_guest_token()
    # Tamper a character in the MIDDLE of the token, not the last one.
    # The very last character of a base64-encoded signature can include
    # "padding" bits that get discarded during decoding -- flipping only
    # those bits sometimes decodes to the exact same bytes, making
    # verification spuriously still succeed (an intermittent false-pass,
    # not a real security issue). Tampering a middle character always
    # falls within real signature data, so this is deterministic.
    pos = len(token) // 2
    tampered = token[:pos] + ("X" if token[pos] != "X" else "Y") + token[pos + 1:]
    assert verify_guest_token(tampered) is None

def test_non_guest_token_rejected():
    assert verify_guest_token("not_a_guest_token") is None
