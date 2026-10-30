import pytest

from app import services as svc


def _agent(db, uniq, **spec):
    a = svc.create_agent(db, uniq("Agente"), "objetivo", "saída")
    if spec:
        svc.design_agent(db, a.slug, spec)
    return a


def test_versions_only_when_spec_changes(db, uniq):
    a = _agent(db, uniq)
    assert a.current_version == 1
    svc.design_agent(db, a.slug, {"instructions": "x"})
    assert a.current_version == 2
    svc.design_agent(db, a.slug, {"instructions": "x"})
    assert a.current_version == 2


def test_merge_patch_through_design(db, uniq):
    a = _agent(db, uniq, llm={"connection": "c1", "model": "old"})
    svc.design_agent(db, a.slug, {"llm": {"connection": "c2", "model": None}})
    assert svc.spec_of(a)["llm"] == {"connection": "c2", "model": "", "temperature": 0.2}


def test_unknown_field_rejected(db, uniq):
    a = _agent(db, uniq)
    with pytest.raises(svc.PlatformError, match="desconhecidos"):
        svc.design_agent(db, a.slug, {"instrucoes": "x"})


def test_rollback_creates_new_version(db, uniq):
    a = _agent(db, uniq, instructions="v2")
    svc.design_agent(db, a.slug, {"instructions": "v3"})
    svc.rollback_agent(db, a.slug, 2)
    assert a.current_version == 4 and svc.spec_of(a)["instructions"] == "v2"


def test_sub_agent_must_exist_and_not_self(db, uniq):
    a = _agent(db, uniq)
    with pytest.raises(svc.PlatformError):
        svc.design_agent(db, a.slug, {"sub_agents": ["nao-existe-xyz"]})
    with pytest.raises(svc.PlatformError):
        svc.design_agent(db, a.slug, {"sub_agents": [a.slug]})


def test_promotion_gate(db, uniq, fake_docker):
    a = _agent(db, uniq, instructions="oi")
    with pytest.raises(svc.PlatformError, match="Promoção bloqueada"):
        svc.promote(db, a.slug)
    run = svc.run_tests(db, a.slug)  # stage + smoke; o fake não responde HTTP, então reprova
    assert run.status == "failed"
    with pytest.raises(svc.PlatformError, match="Promoção bloqueada"):
        svc.promote(db, a.slug)


def test_harness_agent_ships_with_mock_job(db, uniq, fake_docker):
    a = _agent(db, uniq, harness={"id": "codex"}, tests=[{"input": "faça algo", "expect_contains": "zzz"}])
    steps = svc.ship(db, a.slug)
    assert steps[-1]["step"] == "prod"
    # sem conexão: roda na imagem base (mock) e a checagem de conteúdo não se aplica
    assert fake_docker.jobs[-1]["image"].endswith("harness-base:latest")
    assert "(mock)" in a.tests[0].summary


def test_apply_document_is_idempotent(db, uniq):
    slug = uniq("gitops")
    doc = {"skills": [{"name": slug + "-sk", "content": "c"}],
           "agents": [{"name": "GitOps", "slug": slug, "objective": "o", "final_output": "f",
                       "spec": {"instructions": "i", "skills": [slug + "-sk"]}}]}
    first = svc.apply_document(db, doc)
    assert first[-1]["action"] == "created"
    again = svc.apply_document(db, doc)
    assert again[-1]["action"] == "unchanged"
    doc["agents"][0]["spec"]["instructions"] = "i2"
    assert svc.apply_document(db, doc)[-1]["action"] == "updated"


def test_templates_are_valid(db):
    items = svc.list_templates()
    ids = {t["id"] for t in items}
    assert {"doc-qa", "platform-dashboard", "code-fixer", "ticket-triage", "sql-analyst", "research-team"} <= ids
    for t in items:
        doc = svc.get_template(t["id"])["document"]
        for ag in doc["agents"]:
            from app import spec
            spec.validate(ag.get("spec") or {})


def test_apply_template_multi_agent(db):
    out = svc.apply_template(db, "research-team")
    orch = svc.get_agent(db, "research-team")
    assert orch.kind == "multi" and len([o for o in out if o["kind"] == "agent"]) == 4


def test_meeting_notes_template_applies_in_mock_mode(db):
    assert "meeting-notes" in {item["id"] for item in svc.list_templates()}
    svc.apply_template(db, "meeting-notes")
    agent = svc.get_agent(db, "meeting-notes")
    config = svc.spec_of(agent)
    assert not config["llm"].get("connection")
    assert len(config["tests"]) >= 2
    assert all(case.get("judge") for case in config["tests"])
    assert "action_items" in agent.final_output


def test_data_studio_template_in_sync():
    """templates/data-studio.yaml é gerado de examples/data-studio/*.md — rode make_template.py ao editá-los."""
    import pathlib

    import yaml
    root = pathlib.Path(__file__).parent.parent
    t = yaml.safe_load((root / "templates" / "data-studio.yaml").read_text(encoding="utf-8"))
    agent = t["document"]["agents"][0]
    assert agent["spec"]["instructions"] == (root / "examples/data-studio/instructions.md").read_text(encoding="utf-8")
    contents = {s["name"]: s["content"] for s in t["document"]["skills"]}
    assert contents["dashboard-design"] == (root / "examples/data-studio/skill-dashboard.md").read_text(encoding="utf-8")
