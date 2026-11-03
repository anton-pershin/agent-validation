from __future__ import annotations

import pytest

from agent_validation.metric_names import metric_key


def test_single_and_multi_instance_names() -> None:
    assert metric_key("VM1") == "VM1/unperturbed_overall_success_rate"
    assert metric_key("VM5", "concise") == "VM5/prompt_variant_overall_success_rate_loss/concise"
    assert (
        metric_key("VM7", "pick_up_failure")
        == "VM7/transient_failure_overall_success_rate_loss/pick_up_failure"
    )


@pytest.mark.parametrize(
    ("vm_id", "instance_id"),
    [("VM99", None), ("VM5", None), ("VM1", "unexpected")],
)
def test_invalid_metric_key_inputs(vm_id: str, instance_id: str | None) -> None:
    with pytest.raises(ValueError):
        metric_key(vm_id, instance_id)
