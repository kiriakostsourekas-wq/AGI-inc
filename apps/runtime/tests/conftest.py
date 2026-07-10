from datetime import UTC, datetime
from decimal import Decimal

import pytest
from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    FrozenSecurityClock,
    ToolName,
    TrustedTargetKind,
    uuid7,
)

from trust_runtime.effects import TrustedTargetDescriptor
from trust_runtime.fixtures import reference_task_contract


@pytest.fixture
def clock() -> FrozenSecurityClock:
    return FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))


@pytest.fixture
def contract():
    return reference_task_contract()


@pytest.fixture
def booking_action(contract):
    return ActionProposal(
        run_id=uuid7(),
        step_number=7,
        plan_version=1,
        observation_id=uuid7(),
        observation_hash="a" * 64,
        tool=ToolName.CLICK,
        target_description="Confirm replacement flight NS451",
        coordinates_normalized=(720, 810),
        grounding_confidence=Decimal("0.96"),
        decision_summary="Confirm the exact itinerary currently shown after approval.",
    )


@pytest.fixture
def booking_target() -> TrustedTargetDescriptor:
    return TrustedTargetDescriptor(
        target_kind=TrustedTargetKind.BOOKING_CONFIRM,
        origin="http://northstar.localhost:3001",
        trusted_target_id="northstar.confirm-rebooking",
        context=BookingCommitContext(
            traveler_id="traveler_maya_chen",
            reservation_id="NST-P7Q4M2",
            offer_version="offer-v1",
            marketing_carrier="Northstar Air",
            operating_carrier="Northstar Air",
            flight_id="NS451",
            origin_airport="SFO",
            destination_airport="SEA",
            departure=datetime(2030, 6, 14, 14, 10, tzinfo=UTC),
            arrival=datetime(2030, 6, 14, 16, 15, tzinfo=UTC),
            stop_count=0,
            cabin="economy",
            fare_class="Y",
            seat_type="aisle",
            base_fare_minor=35_000,
            taxes_and_fees_minor=3_900,
            total_additional_cost_minor=38_900,
            currency="USD",
        ),
    )
