import unittest

from pydantic import ValidationError

from app.modules.auth.schemas import LoginIn, RegisterIn, ResetIn


class PasswordPolicyTests(unittest.TestCase):
    def test_registration_and_reset_accept_six_through_128_characters(self):
        for length in (6, 9, 10, 128):
            with self.subTest(length=length):
                password = "x" * length
                RegisterIn(username="sample_user", display_name="测试", invite_code="i" * 20, password=password)
                ResetIn(token="t" * 20, password=password)

    def test_registration_and_reset_reject_outside_length_limits(self):
        for length in (0, 5, 129):
            with self.subTest(length=length):
                with self.assertRaises(ValidationError):
                    RegisterIn(username="sample_user", display_name="测试", invite_code="i" * 20, password="x" * length)
                with self.assertRaises(ValidationError):
                    ResetIn(token="t" * 20, password="x" * length)

    def test_login_still_accepts_existing_short_passwords(self):
        LoginIn(username="sample_user", password="x")
