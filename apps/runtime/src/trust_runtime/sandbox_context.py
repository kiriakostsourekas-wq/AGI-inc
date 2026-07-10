"""Canonical context shared by sandbox approval binding and the durable gateway."""

from decimal import Decimal

from trust_contracts import BookingCommitContext, sha256_hex


def sandbox_approval_context_hash(*, run_id: str, context: BookingCommitContext) -> str:
    payload = {
        "runId": run_id,
        "travelerId": context.traveler_id,
        "reservationId": context.reservation_id,
        "flightId": context.flight_id,
        "airline": context.marketing_carrier,
        "marketingCarrier": context.marketing_carrier,
        "operatingCarrier": context.operating_carrier,
        "offerVersion": context.offer_version,
        "origin": context.origin_airport,
        "destination": context.destination_airport,
        "departure": context.departure.isoformat(),
        "arrival": context.arrival.isoformat(),
        "stopCount": context.stop_count,
        "cabin": context.cabin,
        "fareClass": context.fare_class,
        "seatType": context.seat_type,
        "amount": format(Decimal(context.total_additional_cost_minor) / 100, ".2f"),
        "baseFareAmount": format(Decimal(context.base_fare_minor) / 100, ".2f"),
        "taxesAndFeesAmount": format(Decimal(context.taxes_and_fees_minor) / 100, ".2f"),
        "currency": context.currency,
    }
    return sha256_hex(payload)
