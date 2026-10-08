---
name: booking-check
description: Check one Fabrikam Makerspace booking request using BR-001..BR-003. Use for equipment booking requests.
---

# Booking check

1. Call `get_booking_request` with the request id, then `get_member` with its member id.
2. Call `verify_equipment_availability` with the equipment type and the requested window.
3. Apply BR-001 (active membership), BR-002 (slot availability) and BR-003 (ownership) and cite every BR that fired.
