from core import dashboard_auth_vps as auth


def test_only_final_booking_confirmation_is_public() -> None:
    assert auth.is_public_path("/bookings/confirm") is True
    assert auth.is_public_path("/bookings") is False
    assert auth.is_public_path("/bookings/confirm/anything") is False
