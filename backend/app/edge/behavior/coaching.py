"""Plain-language coaching text for behavior events (rules.md section 4).

Describes the pattern and the benefit, never blame.
"""
from __future__ import annotations

from app.shared.enums import BehaviorEventType

_FALLBACK = "Unusual usage pattern detected this shift."


def coaching_message(event_type: str, magnitude: float) -> str:
    messages = {
        BehaviorEventType.EXCESSIVE_IDLING.value: (
            f"Engine idled {magnitude:.0f} min with no activity. "
            "Using auto-idle shutdown while waiting reduces fuel use."
        ),
        BehaviorEventType.REPEATED_OVERLOADING.value: (
            f"Overload incidents: {magnitude:.0f} this shift. "
            "Loading within rated capacity reduces bucket wear and cycle time."
        ),
        BehaviorEventType.HIGH_RPM_TRAVEL.value: (
            f"High-RPM travel sustained {magnitude:.0f} min. "
            "Reducing engine speed while travelling lowers fuel consumption."
        ),
        BehaviorEventType.REPEATED_SEATBELT_VIOLATION.value: (
            f"Seatbelt unfastened {magnitude:.0f} times this shift. "
            "Keeping the seatbelt fastened while the machine can move keeps you inside the protected cab."
        ),
        BehaviorEventType.HIGH_IDLE_RATIO.value: (
            f"Shift idle ratio {magnitude:.1%} is above your typical range. "
            "Reviewing task order or truck timing may reduce engine-on waiting."
        ),
    }
    return messages.get(event_type, _FALLBACK)
