from app.auth.passwords import hash_password, verify_password


def test_password_hash_and_verification() -> None:
    password = "A-Valid-Password-123!"
    password_hash = hash_password(password)

    assert password_hash != password
    assert password_hash.startswith("$argon2")
    assert verify_password(password, password_hash)


def test_incorrect_password_is_rejected() -> None:
    password_hash = hash_password("Correct-Password-123!")

    assert not verify_password("Incorrect-Password-123!", password_hash)


def test_identical_passwords_receive_different_salted_hashes() -> None:
    password = "Same-Password-123!"

    assert hash_password(password) != hash_password(password)


def test_malformed_hash_is_rejected_safely() -> None:
    assert not verify_password("password", "not-a-password-hash")
