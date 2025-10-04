"""
Resilience patterns for error recovery
"""

import asyncio
import functools
from typing import Callable, Any


def with_retry(max_attempts: int = 3, delay: float = 1.0):
    """
    Decorator for retrying failed operations

    Args:
        max_attempts: Maximum number of retry attempts
        delay: Delay between retries in seconds
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(delay * (2 ** attempt))
                    else:
                        raise

            raise last_exception

        return wrapper
    return decorator


async def book_appointment_with_fallback(
    clinic_id: str,
    phone: str,
    date: str,
    time: str
) -> dict:
    """
    Book appointment with fallback on failure

    Args:
        clinic_id: Clinic identifier
        phone: Patient phone number
        date: Appointment date
        time: Appointment time

    Returns:
        Booking result
    """
    try:
        from .appointments import SimpleAppointmentBooking
        booking = SimpleAppointmentBooking()

        result = await booking.book_appointment(
            clinic_id=clinic_id,
            patient_phone=phone,
            requested_date=date,
            requested_time=time
        )

        return result

    except Exception as e:
        # Return fallback response
        return {
            'success': False,
            'message': 'El sistema no está disponible en este momento. Por favor intente más tarde.'
        }
