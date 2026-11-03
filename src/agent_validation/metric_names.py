"""Stable VM identifiers mapped to informative response keys."""

from __future__ import annotations

_NAMES = {
    "VM1": "unperturbed_overall_success_rate",
    "VM2": "error_flag_precision",
    "VM3": "error_flag_recall",
    "VM4": "repeated_run_overall_success_rate_range",
    "VM5": "prompt_variant_overall_success_rate_loss",
    "VM6": "prompt_variant_overall_success_rate_range",
    "VM7": "transient_failure_overall_success_rate_loss",
    "VM8": "transient_failure_overall_success_rate_range",
    "VM9": "maximum_action_attempts",
    "VM10": "timely_final_response_rate",
}
_INSTANCE_METRICS = {"VM5", "VM7"}


def metric_key(vm_id: str, instance_id: str | None = None) -> str:
    """Return the public key for one VM or one variant/scenario instance."""
    try:
        name = _NAMES[vm_id]
    except KeyError as exc:
        raise ValueError(f"Unknown validation metric: {vm_id}") from exc
    if vm_id in _INSTANCE_METRICS:
        if not instance_id:
            raise ValueError(f"{vm_id} requires an instance identifier")
        return f"{vm_id}/{name}/{instance_id}"
    if instance_id is not None:
        raise ValueError(f"{vm_id} does not accept an instance identifier")
    return f"{vm_id}/{name}"
