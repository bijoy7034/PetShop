from helpers.request import client_ip, request_id
from repository.audit_repo import AuditRepository


def record(
    action,
    resource_type,
    resource_id=None,
    actor=None,
    before=None,
    after=None,
    request=None,
):
    return AuditRepository.append(
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        actor=actor,
        before=before,
        after=after,
        request_id=request_id(request),
        ip=client_ip(request),
    )
