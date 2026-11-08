from helpdesk.api.app import build_router
from helpdesk.api.router import Request


def test_create_and_list(services, customer):
    router = build_router(services)
    r = router.handle("POST", "/tickets", Request(body={"title": "Ошибка"}, user_id=customer.id))
    assert r.status == 200
    assert len(router.handle("GET", "/tickets", Request()).body) == 1


def test_validation_is_422(services, customer):
    r = build_router(services).handle("POST", "/tickets", Request(body={"title": ""}, user_id=customer.id))
    assert r.status == 422
