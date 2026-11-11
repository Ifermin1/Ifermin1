"""Grupos de líderes: guardar la configuración de copia con nombre y cambiar de grupo con un clic."""
from httpx import AsyncClient


async def test_save_current_activate_other_and_come_back(client: AsyncClient, container):
    rep, b, groups = container.replication, container.bridge, container.groups
    b.noise = 0
    await container.accounts.sync_once()
    await b.set_master("Sim101")
    rep.link("Sim101", "Sim102", 2.0)
    rep.link("Sim101", "Sim103", 1.0)
    # guardar lo que hay ahora como grupo
    r = await client.post("/api/groups/current", json={"name": "Mañana"})
    assert r.status_code == 201 and r.json()["master"] == "Sim101" and len(r.json()["followers"]) == 2
    g1 = r.json()["id"]
    st = (await client.get("/api/groups")).json()
    assert st["active_id"] == g1 and st["drift"] is False
    # un segundo grupo, con otra líder
    r = await client.post("/api/groups", json={"name": "Tarde", "master": "Sim103", "followers": [{"account": "Sim102", "multiplier": 1}]})
    assert r.status_code == 201
    g2 = r.json()["id"]
    assert (await client.post("/api/groups", json={"name": "tarde", "master": "Sim103"})).status_code == 400   # nombre repetido
    # activar el segundo: la líder cambia en el addon, Sim102 copia x1 a Sim103, las reglas hacia Sim101 quedan apagadas (no borradas)
    r = await client.post(f"/api/groups/{g2}/activate")
    assert r.status_code == 200 and r.json()["on"] == ["Sim102"] and "Sim103" in r.json()["off"]
    assert b.health.master_account == "Sim103"
    assert rep.find_link("Sim103", "Sim102").enabled and rep.find_link("Sim103", "Sim102").multiplier == 1.0
    assert rep.find_link("Sim101", "Sim102").enabled is False and rep.find_link("Sim101", "Sim103").enabled is False
    assert (await client.get("/api/health")).json()["group"]["name"] == "Tarde"
    # volver al primero: Sim101 líder, Sim102 x2 y Sim103 x1 copiando, la regla Sim103→Sim102 apagada
    await client.post(f"/api/groups/{g1}/activate")
    assert b.health.master_account == "Sim101"
    assert rep.find_link("Sim101", "Sim102").enabled and rep.find_link("Sim101", "Sim102").multiplier == 2.0
    assert rep.find_link("Sim101", "Sim103").enabled and rep.find_link("Sim103", "Sim102").enabled is False
    assert any(a.event_type == "GROUP_ACTIVATED" for a in container.audit.recent(10))
    # un cambio a mano se nota como "drift"
    rep.update_rule(rep.find_link("Sim101", "Sim103").id, enabled=False)
    assert (await client.get("/api/groups")).json()["drift"] is True
    # editar y borrar
    r = await client.put(f"/api/groups/{g2}", json={"name": "Tarde 2", "master": "Sim103", "followers": []})
    assert r.status_code == 200 and r.json()["name"] == "Tarde 2"
    assert (await client.delete(f"/api/groups/{g2}")).status_code == 204
    assert (await client.delete(f"/api/groups/{g2}")).status_code == 404
    assert len((await client.get("/api/groups")).json()["groups"]) == 1


async def test_groups_survive_restart(container):
    from tradepilot.services.group_service import GroupService
    container.groups.save("Noche", "Sim101", [{"account": "Sim102", "multiplier": 3}])
    again = GroupService(container.store, container.accounts, container.replication, container.bridge, container.audit)
    assert again.groups[0]["name"] == "Noche" and again.groups[0]["followers"][0]["multiplier"] == 3.0
