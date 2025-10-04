"""
Unit Tests for Reservation Management Tools

Comprehensive test suite for reservation tools including:
- Availability checking
- Appointment booking
- Hold management
- Multi-stage appointments
- Cancellations and rescheduling
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
import json
import uuid

from app.services.reservation_tools import ReservationTools, ReservationStatus
from app.services.hold_cleanup_job import HoldCleanupJob, HoldMonitor
from app.services.whatsapp_confirmation_service import WhatsAppConfirmationService
from app.services.langgraph_appointment_handler import LangGraphAppointmentHandler


# Test fixtures

@pytest.fixture
def clinic_id():
    """Test clinic ID"""
    return "e0c84f56-235d-49f2-9a44-37c1be579afc"


@pytest.fixture
def patient_id():
    """Test patient ID"""
    return str(uuid.uuid4())


@pytest.fixture
def mock_supabase():
    """Mock Supabase client"""
    mock = MagicMock()

    # Mock table operations
    mock.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    mock.table.return_value.insert.return_value.execute.return_value.data = [{"id": str(uuid.uuid4())}]
    mock.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [{"status": "updated"}]
    mock.table.return_value.delete.return_value.eq.return_value.execute.return_value.data = []

    return mock


@pytest.fixture
def mock_services():
    """Mock service dependencies"""
    services = {
        'booking_service': AsyncMock(),
        'unified_service': AsyncMock(),
        'calendar_service': AsyncMock(),
        'scheduler': AsyncMock(),
        'conflict_detector': AsyncMock(),
        'session_manager': AsyncMock()
    }

    # Set default return values
    services['calendar_service'].ask_availability.return_value = True
    services['calendar_service'].hold_slot.return_value = True
    services['calendar_service'].reserve_slot.return_value = True
    services['scheduler'].find_available_slots.return_value = [
        {
            "datetime": (datetime.now() + timedelta(days=1)).isoformat(),
            "doctor_id": str(uuid.uuid4()),
            "doctor_name": "Dr. Smith"
        }
    ]

    return services


@pytest.fixture
async def reservation_tools(clinic_id, patient_id, mock_supabase, mock_services):
    """Create reservation tools instance with mocked dependencies"""
    with patch('app.services.reservation_tools.create_supabase_client', return_value=mock_supabase):
        with patch('app.services.reservation_tools.AppointmentBookingService', return_value=mock_services['booking_service']):
            with patch('app.services.reservation_tools.UnifiedAppointmentService', return_value=mock_services['unified_service']):
                with patch('app.services.reservation_tools.ExternalCalendarService', return_value=mock_services['calendar_service']):
                    with patch('app.services.reservation_tools.IntelligentScheduler', return_value=mock_services['scheduler']):
                        with patch('app.services.reservation_tools.RealtimeConflictDetector', return_value=mock_services['conflict_detector']):
                            with patch('app.services.reservation_tools.RedisSessionManager', return_value=mock_services['session_manager']):
                                tools = ReservationTools(clinic_id, patient_id)
                                # Replace services with mocks after initialization
                                tools.booking_service = mock_services['booking_service']
                                tools.unified_service = mock_services['unified_service']
                                tools.calendar_service = mock_services['calendar_service']
                                tools.scheduler = mock_services['scheduler']
                                tools.conflict_detector = mock_services['conflict_detector']
                                tools.session_manager = mock_services['session_manager']
                                tools.supabase = mock_supabase
                                return tools


# Test Availability Checking

class TestAvailabilityChecking:
    """Test availability checking functionality"""

    @pytest.mark.asyncio
    async def test_check_availability_success(self, reservation_tools, mock_supabase):
        """Test successful availability check"""
        # Setup mock data
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {
                "id": str(uuid.uuid4()),
                "name": "Dental Cleaning",
                "duration_minutes": 60,
                "base_price": 100,
                "stage_config": {}
            }
        ]

        reservation_tools.scheduler.find_available_slots.return_value = [
            {
                "datetime": (datetime.now() + timedelta(days=1, hours=10)).isoformat(),
                "doctor_id": str(uuid.uuid4()),
                "doctor_name": "Dr. Smith"
            },
            {
                "datetime": (datetime.now() + timedelta(days=1, hours=14)).isoformat(),
                "doctor_id": str(uuid.uuid4()),
                "doctor_name": "Dr. Jones"
            }
        ]

        # Test
        result = await reservation_tools.check_availability_tool(
            service_name="Dental Cleaning",
            preferred_date=(datetime.now() + timedelta(days=1)).date().isoformat(),
            time_preference="morning"
        )

        # Assertions
        assert result["success"] is True
        assert len(result["available_slots"]) > 0
        assert result["service"]["name"] == "Dental Cleaning"
        assert result["recommendation"] is not None

    @pytest.mark.asyncio
    async def test_check_availability_no_slots(self, reservation_tools, mock_supabase):
        """Test availability check with no available slots"""
        # Setup
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {"id": str(uuid.uuid4()), "name": "Root Canal", "duration_minutes": 120}
        ]
        reservation_tools.scheduler.find_available_slots.return_value = []

        # Test
        result = await reservation_tools.check_availability_tool(
            service_name="Root Canal",
            preferred_date=(datetime.now() + timedelta(days=1)).date().isoformat()
        )

        # Assertions
        assert result["success"] is True
        assert len(result["available_slots"]) == 0
        assert result["recommendation"] is None

    @pytest.mark.asyncio
    async def test_check_availability_service_not_found(self, reservation_tools, mock_supabase):
        """Test availability check for non-existent service"""
        # Setup
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = []

        # Test
        result = await reservation_tools.check_availability_tool(
            service_name="Non-existent Service"
        )

        # Assertions
        assert result["success"] is False
        assert "not found" in result["error"]
        assert result["available_slots"] == []

    @pytest.mark.asyncio
    async def test_check_availability_with_time_preference(self, reservation_tools, mock_supabase):
        """Test availability check with time preference filtering"""
        # Setup
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {"id": str(uuid.uuid4()), "name": "Checkup", "duration_minutes": 30}
        ]

        # Mix of morning and afternoon slots
        base_date = datetime.now() + timedelta(days=1)
        reservation_tools.scheduler.find_available_slots.return_value = [
            {"datetime": base_date.replace(hour=9).isoformat()},  # Morning
            {"datetime": base_date.replace(hour=10).isoformat()},  # Morning
            {"datetime": base_date.replace(hour=14).isoformat()},  # Afternoon
            {"datetime": base_date.replace(hour=15).isoformat()},  # Afternoon
        ]

        # Test morning preference
        result = await reservation_tools.check_availability_tool(
            service_name="Checkup",
            time_preference="morning"
        )

        # Assertions - should only have morning slots
        assert result["success"] is True
        for slot in result["available_slots"]:
            dt = datetime.fromisoformat(slot["datetime"])
            assert 6 <= dt.hour < 12  # Morning hours


# Test Appointment Booking

class TestAppointmentBooking:
    """Test appointment booking functionality"""

    @pytest.mark.asyncio
    async def test_book_appointment_success(self, reservation_tools, mock_supabase):
        """Test successful appointment booking"""
        # Setup
        appointment_id = str(uuid.uuid4())
        service_id = str(uuid.uuid4())
        hold_id = str(uuid.uuid4())

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": service_id,
                "name": "Dental Cleaning",
                "duration_minutes": 60,
                "stage_config": {}
            }
        ]

        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": hold_id}
        ]

        reservation_tools.booking_service.book_appointment.return_value = {
            "id": appointment_id,
            "scheduled_at": datetime.now().isoformat()
        }

        # Test
        patient_info = {
            "name": "John Doe",
            "phone": "+1234567890",
            "email": "john@example.com"
        }

        result = await reservation_tools.book_appointment_tool(
            patient_info=patient_info,
            service_id=service_id,
            datetime_str=(datetime.now() + timedelta(days=1)).isoformat(),
            notes="Test appointment"
        )

        # Assertions
        assert result["success"] is True
        assert result["appointment_id"] == appointment_id
        assert "confirmation_message" in result

    @pytest.mark.asyncio
    async def test_book_appointment_with_existing_hold(self, reservation_tools, mock_supabase):
        """Test booking with an existing hold"""
        # Setup
        hold_id = str(uuid.uuid4())
        appointment_id = str(uuid.uuid4())
        service_id = str(uuid.uuid4())

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": service_id,
                "name": "Root Canal",
                "duration_minutes": 120,
                "stage_config": {}
            }
        ]

        reservation_tools.booking_service.book_appointment.return_value = {
            "id": appointment_id
        }

        # Test
        result = await reservation_tools.book_appointment_tool(
            patient_info={"name": "Jane Doe", "phone": "+1234567890"},
            service_id=service_id,
            datetime_str=datetime.now().isoformat(),
            hold_id=hold_id
        )

        # Assertions
        assert result["success"] is True
        # Should not create a new hold when hold_id is provided
        reservation_tools.create_appointment_hold_tool = AsyncMock()
        reservation_tools.create_appointment_hold_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_book_multi_stage_appointment(self, reservation_tools, mock_supabase):
        """Test booking multi-stage appointments"""
        # Setup
        service_id = str(uuid.uuid4())
        base_date = datetime.now() + timedelta(days=7)

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": service_id,
                "name": "Orthodontic Treatment",
                "duration_minutes": 45,
                "stage_config": {
                    "total_stages": 3,
                    "days_between_stages": 14
                }
            }
        ]

        # Mock successful multi-stage booking
        appointment_ids = [str(uuid.uuid4()) for _ in range(3)]
        mock_supabase.table.return_value.insert.return_value.execute.side_effect = [
            MagicMock(data=[{"id": hold_id}]) for hold_id in [str(uuid.uuid4())]
        ] + [
            MagicMock(data=[{"id": apt_id, "stage_number": i+1}])
            for i, apt_id in enumerate(appointment_ids)
        ]

        # Test
        result = await reservation_tools.book_appointment_tool(
            patient_info={"name": "Patient", "phone": "+1234567890"},
            service_id=service_id,
            datetime_str=base_date.isoformat()
        )

        # Assertions
        assert result["success"] is True
        assert result.get("is_multi_stage") is True
        assert result.get("total_stages") == 3
        assert len(result.get("appointment_ids", [])) == 3

    @pytest.mark.asyncio
    async def test_book_appointment_service_not_found(self, reservation_tools, mock_supabase):
        """Test booking with non-existent service"""
        # Setup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []

        # Test
        result = await reservation_tools.book_appointment_tool(
            patient_info={"name": "Patient", "phone": "+1234567890"},
            service_id=str(uuid.uuid4()),
            datetime_str=datetime.now().isoformat()
        )

        # Assertions
        assert result["success"] is False
        assert "Service not found" in result["error"]


# Test Hold Management

class TestHoldManagement:
    """Test appointment hold management"""

    @pytest.mark.asyncio
    async def test_create_hold_success(self, reservation_tools, mock_supabase):
        """Test successful hold creation"""
        # Setup
        hold_id = str(uuid.uuid4())
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {
                "id": hold_id,
                "status": "active",
                "expire_at": (datetime.now() + timedelta(minutes=15)).isoformat()
            }
        ]

        # Test
        result = await reservation_tools.create_appointment_hold_tool(
            slot_datetime=(datetime.now() + timedelta(hours=2)).isoformat(),
            duration_minutes=60,
            hold_duration_minutes=15
        )

        # Assertions
        assert result["success"] is True
        assert result["hold_id"] == hold_id
        assert result["duration_minutes"] == 15

    @pytest.mark.asyncio
    async def test_confirm_hold(self, reservation_tools, mock_supabase):
        """Test hold confirmation"""
        # Setup
        hold_id = str(uuid.uuid4())
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": hold_id, "status": "confirmed"}
        ]

        # Test
        result = await reservation_tools.confirm_appointment_hold_tool(
            hold_id=hold_id,
            confirmation_data={"appointment_id": str(uuid.uuid4())}
        )

        # Assertions
        assert result["success"] is True
        assert result["status"] == "confirmed"

    @pytest.mark.asyncio
    async def test_release_hold(self, reservation_tools, mock_supabase):
        """Test hold release"""
        # Setup
        hold_id = str(uuid.uuid4())
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": hold_id, "status": "active"}
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": hold_id, "status": "released"}
        ]

        # Test
        result = await reservation_tools.release_appointment_hold_tool(
            hold_id=hold_id,
            reason="Patient cancelled"
        )

        # Assertions
        assert result["success"] is True
        assert result["status"] == "released"
        assert result["reason"] == "Patient cancelled"

    @pytest.mark.asyncio
    async def test_hold_cleanup_job(self, mock_supabase):
        """Test hold cleanup job"""
        with patch('app.services.hold_cleanup_job.create_supabase_client', return_value=mock_supabase):
            # Setup
            expired_holds = [
                {
                    "id": str(uuid.uuid4()),
                    "status": "active",
                    "expire_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
                    "clinic_id": "test_clinic",
                    "doctor_id": str(uuid.uuid4())
                }
            ]

            mock_supabase.table.return_value.select.return_value.eq.return_value.lt.return_value.execute.return_value.data = expired_holds
            mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
                {"id": hold["id"], "status": "expired"} for hold in expired_holds
            ]

            # Test
            cleanup_job = HoldCleanupJob(run_interval_minutes=5)
            stats = await cleanup_job.cleanup_expired_holds()

            # Assertions
            assert stats["expired_holds"] == 1
            assert stats["released_holds"] == 1
            assert stats["errors"] == 0


# Test Cancellations and Rescheduling

class TestCancellationsAndRescheduling:
    """Test appointment cancellations and rescheduling"""

    @pytest.mark.asyncio
    async def test_cancel_appointment(self, reservation_tools, mock_supabase):
        """Test appointment cancellation"""
        # Setup
        appointment_id = str(uuid.uuid4())
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": appointment_id,
                "status": "scheduled",
                "service_name": "Dental Cleaning"
            }
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": appointment_id, "status": "cancelled"}
        ]

        # Test
        result = await reservation_tools.cancel_appointment_tool(
            appointment_id=appointment_id,
            cancellation_reason="Patient request"
        )

        # Assertions
        assert result["success"] is True
        assert appointment_id in result["cancelled_appointment_ids"]

    @pytest.mark.asyncio
    async def test_cancel_multi_stage_appointment(self, reservation_tools, mock_supabase):
        """Test cancelling all stages of multi-stage appointment"""
        # Setup
        parent_id = str(uuid.uuid4())
        child_ids = [str(uuid.uuid4()) for _ in range(2)]

        all_appointments = [
            {"id": parent_id, "stage_number": 1},
            {"id": child_ids[0], "stage_number": 2, "parent_appointment_id": parent_id},
            {"id": child_ids[1], "stage_number": 3, "parent_appointment_id": parent_id}
        ]

        # Mock getting appointment
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": parent_id, "parent_appointment_id": None}
        ]

        # Mock getting related appointments
        mock_supabase.table.return_value.select.return_value.or_.return_value.order.return_value.execute.return_value.data = all_appointments

        # Mock updates
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": apt["id"], "status": "cancelled"} for apt in all_appointments
        ]

        # Test
        result = await reservation_tools.cancel_appointment_tool(
            appointment_id=parent_id,
            cancellation_reason="Patient request",
            cancel_all_stages=True
        )

        # Assertions
        assert result["success"] is True
        assert len(result["cancelled_appointment_ids"]) == 3

    @pytest.mark.asyncio
    async def test_reschedule_appointment(self, reservation_tools, mock_supabase):
        """Test appointment rescheduling"""
        # Setup
        appointment_id = str(uuid.uuid4())
        old_datetime = datetime.now() + timedelta(days=1)
        new_datetime = datetime.now() + timedelta(days=3)

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": appointment_id,
                "scheduled_at": old_datetime.isoformat(),
                "service_name": "Checkup",
                "duration_minutes": 30
            }
        ]

        # Mock availability check
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {"id": str(uuid.uuid4()), "name": "Checkup", "duration_minutes": 30}
        ]

        reservation_tools.scheduler.find_available_slots.return_value = [
            {"datetime": new_datetime.isoformat()}
        ]

        # Mock hold creation and appointment update
        hold_id = str(uuid.uuid4())
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": hold_id}
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": appointment_id, "status": "rescheduled"}
        ]

        # Test
        result = await reservation_tools.reschedule_appointment_tool(
            appointment_id=appointment_id,
            new_datetime=new_datetime.isoformat(),
            reschedule_reason="Schedule conflict"
        )

        # Assertions
        assert result["success"] is True
        assert result["appointment_id"] == appointment_id
        assert result["new_datetime"] == new_datetime.isoformat()


# Test Search Functionality

class TestSearchFunctionality:
    """Test appointment search functionality"""

    @pytest.mark.asyncio
    async def test_search_appointments_by_phone(self, reservation_tools, mock_supabase):
        """Test searching appointments by phone number"""
        # Setup
        appointments = [
            {
                "id": str(uuid.uuid4()),
                "patient_phone": "+1234567890",
                "service_name": "Cleaning",
                "scheduled_at": (datetime.now() + timedelta(days=1)).isoformat()
            },
            {
                "id": str(uuid.uuid4()),
                "patient_phone": "+1234567890",
                "service_name": "Checkup",
                "scheduled_at": (datetime.now() + timedelta(days=7)).isoformat()
            }
        ]

        mock = mock_supabase.table.return_value.select.return_value
        mock.eq.return_value = mock
        mock.gte.return_value = mock
        mock.lte.return_value = mock
        mock.order.return_value.execute.return_value.data = appointments

        # Test
        result = await reservation_tools.search_appointments_tool(
            patient_phone="+1234567890"
        )

        # Assertions
        assert result["success"] is True
        assert len(result["appointments"]) == 2

    @pytest.mark.asyncio
    async def test_search_appointments_with_date_range(self, reservation_tools, mock_supabase):
        """Test searching appointments with date range"""
        # Setup
        start_date = datetime.now()
        end_date = datetime.now() + timedelta(days=7)

        appointments = [
            {
                "id": str(uuid.uuid4()),
                "scheduled_at": (start_date + timedelta(days=2)).isoformat(),
                "service_name": "Cleaning"
            }
        ]

        mock = mock_supabase.table.return_value.select.return_value
        mock.eq.return_value = mock
        mock.gte.return_value = mock
        mock.lte.return_value = mock
        mock.order.return_value.execute.return_value.data = appointments

        # Test
        result = await reservation_tools.search_appointments_tool(
            date_range={
                "start_date": start_date.date().isoformat(),
                "end_date": end_date.date().isoformat()
            }
        )

        # Assertions
        assert result["success"] is True
        assert len(result["appointments"]) == 1


# Test LangGraph Integration

class TestLangGraphIntegration:
    """Test LangGraph appointment handler integration"""

    @pytest.mark.asyncio
    async def test_langgraph_handler_availability_check(self, clinic_id):
        """Test LangGraph handler for availability checking"""
        with patch('app.services.langgraph_appointment_handler.ReservationTools') as MockReservationTools:
            # Setup
            mock_tools = AsyncMock()
            MockReservationTools.return_value = mock_tools

            mock_tools.check_availability_tool.return_value = {
                "success": True,
                "available_slots": [
                    {"datetime": datetime.now().isoformat(), "doctor_name": "Dr. Smith"}
                ],
                "service": {"name": "Cleaning", "id": str(uuid.uuid4())}
            }

            handler = LangGraphAppointmentHandler(clinic_id)
            handler.reservation_tools = mock_tools

            # Create mock state
            mock_state = MagicMock()
            mock_state.patient = MagicMock(patient_id=str(uuid.uuid4()))
            mock_state.intent = MagicMock(entities={})
            mock_state.workflow = MagicMock(workflow_data={}, pending_actions=[])

            # Test
            result = await handler.handle_appointment_request(
                mock_state,
                "When can I schedule a cleaning?"
            )

            # Assertions
            assert "response" in result
            assert "available" in result["response"].lower()
            assert result.get("next_action") == "await_slot_selection"

    @pytest.mark.asyncio
    async def test_langgraph_handler_booking(self, clinic_id):
        """Test LangGraph handler for appointment booking"""
        with patch('app.services.langgraph_appointment_handler.ReservationTools') as MockReservationTools:
            # Setup
            mock_tools = AsyncMock()
            MockReservationTools.return_value = mock_tools

            appointment_id = str(uuid.uuid4())
            mock_tools.book_appointment_tool.return_value = {
                "success": True,
                "appointment_id": appointment_id,
                "confirmation_message": "Appointment booked!"
            }

            handler = LangGraphAppointmentHandler(clinic_id)
            handler.reservation_tools = mock_tools

            # Create mock state with selected slot
            mock_state = MagicMock()
            mock_state.patient = MagicMock(
                patient_id=str(uuid.uuid4()),
                name="John Doe",
                phone="+1234567890"
            )
            mock_state.intent = MagicMock(entities={})
            mock_state.workflow = MagicMock(
                workflow_data={
                    "selected_slot": {"datetime": datetime.now().isoformat()},
                    "service_info": {"id": str(uuid.uuid4()), "name": "Cleaning"}
                },
                pending_actions=[]
            )

            # Test
            result = await handler.handle_appointment_request(
                mock_state,
                "Book the first slot"
            )

            # Assertions
            assert result.get("booking_complete") is True
            assert "booked" in result["response"].lower()


# Test WhatsApp Confirmation Service

class TestWhatsAppConfirmation:
    """Test WhatsApp confirmation service"""

    @pytest.mark.asyncio
    async def test_send_appointment_confirmation(self, clinic_id, mock_supabase):
        """Test sending appointment confirmation via WhatsApp"""
        with patch('app.services.whatsapp_confirmation_service.create_supabase_client', return_value=mock_supabase):
            with patch('app.services.whatsapp_confirmation_service.aiohttp.ClientSession') as MockSession:
                # Setup
                mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
                    {
                        "whatsapp_config": {
                            "evolution_api_url": "https://api.evolution.com",
                            "evolution_api_key": "test_key",
                            "instance_name": "test_instance"
                        }
                    }
                ]

                mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
                    {
                        "id": str(uuid.uuid4()),
                        "scheduled_at": datetime.now().isoformat(),
                        "service_name": "Cleaning",
                        "doctor_name": "Dr. Smith"
                    }
                ]

                # Mock HTTP response
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.json.return_value = {
                    "key": {"id": "msg_123"},
                    "status": "sent"
                }

                mock_session = AsyncMock()
                mock_session.post.return_value.__aenter__.return_value = mock_response
                MockSession.return_value.__aenter__.return_value = mock_session

                # Test
                service = WhatsAppConfirmationService(clinic_id)
                await service.initialize()

                result = await service.send_appointment_confirmation(
                    appointment_id=str(uuid.uuid4()),
                    patient_phone="+1234567890"
                )

                # Assertions
                assert result["success"] is True
                assert result["message_id"] == "msg_123"

    @pytest.mark.asyncio
    async def test_request_appointment_confirmation(self, clinic_id, mock_supabase):
        """Test requesting appointment confirmation with timeout"""
        with patch('app.services.whatsapp_confirmation_service.create_supabase_client', return_value=mock_supabase):
            with patch('app.services.whatsapp_confirmation_service.aiohttp.ClientSession') as MockSession:
                # Setup
                mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
                    {
                        "whatsapp_config": {
                            "evolution_api_url": "https://api.evolution.com",
                            "evolution_api_key": "test_key",
                            "instance_name": "test_instance"
                        }
                    }
                ]

                # Mock HTTP response
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.json.return_value = {
                    "key": {"id": "msg_456"},
                    "status": "sent"
                }

                mock_session = AsyncMock()
                mock_session.post.return_value.__aenter__.return_value = mock_response
                MockSession.return_value.__aenter__.return_value = mock_session

                # Test
                service = WhatsAppConfirmationService(clinic_id)
                await service.initialize()

                hold_id = str(uuid.uuid4())
                slot_details = {
                    "datetime": datetime.now().isoformat(),
                    "service_name": "Cleaning",
                    "duration_minutes": 60
                }

                result = await service.request_appointment_confirmation(
                    hold_id=hold_id,
                    patient_phone="+1234567890",
                    slot_details=slot_details,
                    timeout_minutes=15
                )

                # Assertions
                assert result["success"] is True
                assert result["message_id"] == "msg_456"


# Test Error Handling

class TestErrorHandling:
    """Test error handling across reservation tools"""

    @pytest.mark.asyncio
    async def test_database_error_handling(self, reservation_tools, mock_supabase):
        """Test handling of database errors"""
        # Setup - simulate database error
        mock_supabase.table.side_effect = Exception("Database connection error")

        # Test
        result = await reservation_tools.check_availability_tool(
            service_name="Test Service"
        )

        # Assertions
        assert result["success"] is False
        assert "Database connection error" in result["error"]

    @pytest.mark.asyncio
    async def test_external_service_error_handling(self, reservation_tools):
        """Test handling of external service errors"""
        # Setup - simulate calendar service error
        reservation_tools.calendar_service.ask_availability.side_effect = Exception("Calendar API error")

        # Mock service lookup to succeed
        reservation_tools.supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {"id": str(uuid.uuid4()), "name": "Test Service", "duration_minutes": 30}
        ]

        # Test - should continue despite calendar error
        result = await reservation_tools.check_availability_tool(
            service_name="Test Service"
        )

        # Assertions - should handle error gracefully
        assert result["success"] is False or len(result.get("available_slots", [])) == 0

    @pytest.mark.asyncio
    async def test_invalid_datetime_handling(self, reservation_tools):
        """Test handling of invalid datetime inputs"""
        # Test with invalid datetime format
        result = await reservation_tools.book_appointment_tool(
            patient_info={"name": "Test", "phone": "123"},
            service_id=str(uuid.uuid4()),
            datetime_str="not-a-valid-datetime"
        )

        # Assertions
        assert result["success"] is False
        assert "error" in result


# Integration Tests

class TestIntegration:
    """Integration tests for complete workflows"""

    @pytest.mark.asyncio
    async def test_complete_booking_workflow(self, reservation_tools, mock_supabase):
        """Test complete workflow from availability check to booking"""
        # Setup
        service_id = str(uuid.uuid4())
        appointment_id = str(uuid.uuid4())
        hold_id = str(uuid.uuid4())

        # Mock service lookup
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {
                "id": service_id,
                "name": "Complete Checkup",
                "duration_minutes": 45,
                "stage_config": {}
            }
        ]

        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": service_id,
                "name": "Complete Checkup",
                "duration_minutes": 45,
                "stage_config": {}
            }
        ]

        # Mock available slots
        slot_datetime = (datetime.now() + timedelta(days=2, hours=10)).isoformat()
        reservation_tools.scheduler.find_available_slots.return_value = [
            {"datetime": slot_datetime, "doctor_id": str(uuid.uuid4())}
        ]

        # Step 1: Check availability
        availability_result = await reservation_tools.check_availability_tool(
            service_name="Complete Checkup",
            preferred_date=(datetime.now() + timedelta(days=2)).date().isoformat()
        )

        assert availability_result["success"] is True
        assert len(availability_result["available_slots"]) > 0

        selected_slot = availability_result["available_slots"][0]

        # Step 2: Create hold
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": hold_id, "status": "active"}
        ]

        hold_result = await reservation_tools.create_appointment_hold_tool(
            slot_datetime=selected_slot["datetime"],
            duration_minutes=45,
            service_id=service_id
        )

        assert hold_result["success"] is True
        assert hold_result["hold_id"] == hold_id

        # Step 3: Book appointment
        reservation_tools.booking_service.book_appointment.return_value = {
            "id": appointment_id,
            "scheduled_at": selected_slot["datetime"]
        }

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": hold_id, "status": "confirmed"}
        ]

        booking_result = await reservation_tools.book_appointment_tool(
            patient_info={
                "name": "Test Patient",
                "phone": "+1234567890",
                "email": "test@example.com"
            },
            service_id=service_id,
            datetime_str=selected_slot["datetime"],
            hold_id=hold_id
        )

        assert booking_result["success"] is True
        assert booking_result["appointment_id"] == appointment_id

    @pytest.mark.asyncio
    async def test_reschedule_workflow(self, reservation_tools, mock_supabase):
        """Test complete rescheduling workflow"""
        # Setup
        appointment_id = str(uuid.uuid4())
        old_datetime = datetime.now() + timedelta(days=1)
        new_datetime = datetime.now() + timedelta(days=3)

        # Step 1: Search for appointment
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": appointment_id,
                "scheduled_at": old_datetime.isoformat(),
                "service_name": "Cleaning",
                "patient_phone": "+1234567890",
                "status": "scheduled"
            }
        ]

        search_result = await reservation_tools.search_appointments_tool(
            patient_phone="+1234567890",
            status="scheduled"
        )

        assert search_result["success"] is True
        assert len(search_result["appointments"]) > 0

        # Step 2: Check new slot availability
        mock_supabase.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            {"id": str(uuid.uuid4()), "name": "Cleaning", "duration_minutes": 60}
        ]

        reservation_tools.scheduler.find_available_slots.return_value = [
            {"datetime": new_datetime.isoformat()}
        ]

        availability_result = await reservation_tools.check_availability_tool(
            service_name="Cleaning",
            preferred_date=new_datetime.date().isoformat()
        )

        assert availability_result["success"] is True

        # Step 3: Reschedule appointment
        hold_id = str(uuid.uuid4())
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": hold_id}
        ]

        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {
                "id": appointment_id,
                "scheduled_at": new_datetime.isoformat(),
                "status": "rescheduled"
            }
        ]

        reschedule_result = await reservation_tools.reschedule_appointment_tool(
            appointment_id=appointment_id,
            new_datetime=new_datetime.isoformat(),
            reschedule_reason="Schedule conflict"
        )

        assert reschedule_result["success"] is True
        assert reschedule_result["new_datetime"] == new_datetime.isoformat()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])