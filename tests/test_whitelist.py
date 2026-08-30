import pytest
from config.settings import settings, is_user_allowed

def test_whitelisted_user_access():
    authorized_id = 454870771039469568
    assert is_user_allowed(authorized_id) is True

def test_unauthorized_user_blocked():
    unauthorized_id = 999999999999999999
    assert is_user_allowed(unauthorized_id) is False

def test_random_user_blocked():
    assert is_user_allowed(123456789) is False
