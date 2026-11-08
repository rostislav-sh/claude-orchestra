def test_internal_comments_hidden_from_customer(services, customer, agent):
    t = services.tickets.create("A", "", customer.id)
    services.comments.add(t.id, customer.id, "помогите")
    services.comments.add(t.id, agent.id, "клиент VIP", internal=True)
    assert len(services.comments.visible(t.id, staff=False)) == 1
    assert len(services.comments.visible(t.id, staff=True)) == 2
