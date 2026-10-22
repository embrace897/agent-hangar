"""Avatares dos agentes: um catálogo de personagens (static/avatars/<id>.webp). Quem cria o agente (a IA, pelo MCP)
escolhe um pelo papel; sem escolha, o papel (nome + objetivo) decide; o dono troca depois na página do agente."""
import re
import unicodedata
import zlib

from .common import PlatformError

# id, rótulo, palavras do papel (sem acento; até 3 letras casam só a palavra inteira, as demais casam o começo)
CATALOG: list[tuple[str, str, tuple[str, ...]]] = [
    ("jornalista", "Jornalista", ("noticia", "news", "jornal", "imprensa", "press", "boletim", "newsletter", "radar",
                                  "clipping", "manchete", "headline")),
    ("cientista-dados", "Cientista de dados", ("dados", "data", "analytic", "sql", "dashboard", "metrica", "metric",
                                               "kpi", "bi", "previs", "forecast", "estatist", "machine learning")),
    ("desenvolvedor", "Desenvolvedor", ("codigo", "code", "dev", "programa", "software", "github", "pull request",
                                        "api", "bug", "refator", "teste", "test")),
    ("suporte", "Suporte", ("suporte", "support", "helpdesk", "ticket", "chamado", "ajuda", "faq", "podcast",
                            "audio", "transcri", "voz", "voice", "radio")),
    ("atendente", "Atendente", ("atendimento", "customer", "cliente", "sac", "call center", "whatsapp", "chat")),
    ("analista-financeiro", "Analista financeiro", ("financ", "contab", "accounting", "fatura", "invoice", "orcament",
                                                    "budget", "cobranca", "investiment", "custo", "cost", "nota fiscal")),
    ("vendedora", "Vendedora", ("venda", "sales", "comercial", "loja", "store", "ecommerce", "produto", "product",
                                "preco", "pricing", "proposta", "renovac", "renewal", "deal")),
    ("cowboy", "Caçador de leads", ("lead", "prospec", "outbound", "sdr", "funil", "funnel")),
    ("advogado", "Advogado", ("juridic", "legal", "contrato", "contract", "lei", "law", "clausula", "advog")),
    ("xerife", "Xerife", ("compliance", "auditor", "audit", "governanc", "politica", "policy", "conformidade")),
    ("policial", "Policial", ("seguranca", "security", "fraude", "fraud", "risco", "risk", "lgpd", "privacidade",
                              "privacy", "dlp")),
    ("hacker", "Hacker ético", ("pentest", "red team", "vulnerab", "hacker", "exploit", "ctf", "ataque")),
    ("bombeiro", "Bombeiro", ("incidente", "incident", "emergencia", "emergency", "alerta", "alert", "on-call",
                              "plantao", "outage")),
    ("salva-vidas", "Salva-vidas", ("monitor", "vigil", "watch", "uptime", "observab", "sentinela")),
    ("eletricista", "Eletricista", ("devops", "deploy", "infraestrutura", "infra", "servidor", "server", "kubernetes",
                                    "docker", "cloud", "rede", "network", "pipeline", "ci")),
    ("engenheiro-obra", "Engenheiro de obras", ("obra", "construc", "construction", "engenharia", "manutenc",
                                                "maintenance", "fabrica", "factory", "industria")),
    ("gerente-projetos", "Gerente de projetos", ("projeto", "project", "cronograma", "sprint", "roadmap",
                                                 "planejament", "entrega", "backlog")),
    ("maestro", "Maestro", ("orquestr", "orchestr", "coorden", "multiagente", "multi-agent", "musica", "music")),
    ("professora", "Professora", ("ensin", "aula", "teach", "educa", "tutor", "treinament", "training", "curso",
                                  "course", "onboarding", "quiz")),
    ("estudante", "Estudante", ("estud", "student", "aprend", "learn", "resum", "summar", "leitura", "paper",
                                "artigo", "article")),
    ("explorador", "Explorador", ("pesquis", "research", "explor", "investig", "busca", "search", "descobr",
                                  "scout", "web")),
    ("pirata", "Pirata", ("scrap", "crawler", "crawl", "garimp", "extrac", "extract")),
    ("concierge", "Concierge", ("agenda", "calendar", "reuniao", "meeting", "assistente", "assistant", "secretar",
                                "email", "e-mail", "gmail", "inbox")),
    ("designer", "Designer", ("design", "criativ", "creative", "arte", "ilustr", "ui", "ux", "imagem", "image",
                              "video", "slide", "apresentac", "logo")),
    ("estilista", "Estilista", ("moda", "fashion", "estilo", "style", "marca", "brand")),
    ("consultora", "Consultora", ("consult", "estrateg", "strateg", "diagnost", "recomend", "recommend")),
    ("executivo", "Executivo", ("executiv", "ceo", "diretor", "director", "gestao", "board", "conselho")),
    ("mentor", "Mentor", ("mentor", "coach", "conselh", "advice", "revis", "review", "critic", "historia", "history",
                          "memoria", "memory", "arquivo", "archive", "legado", "legacy")),
    ("comissaria", "Comissária", ("aeroporto", "airport", "recepc", "boas-vindas", "welcome", "turismo", "tourism", "hotel", "hospedag")),
    ("piloto", "Piloto", ("viagem", "travel", "voo", "flight", "aviac", "airline", "passagem")),
    ("comandante", "Comandante", ("logistic", "rota", "route", "frota", "fleet", "transport", "entrega de carga",
                                  "frete", "shipping")),
    ("astronauta", "Astronauta", ("espac", "space", "inovac", "innovation", "futuro", "laboratorio", "experiment",
                                  "prototip")),
    ("medica", "Médica", ("saude", "health", "medic", "clinic", "paciente", "patient", "hospital", "diagnostico")),
    ("cirurgia", "Cirurgiã", ("cirurg", "surgery", "precisao")),
    ("cirurgiao", "Cirurgião", ("enfermag", "nurse", "uti")),
    ("dentista", "Dentista", ("dent", "odonto")),
    ("farmaceutico", "Farmacêutico", ("farmac", "pharma", "medicament", "remedio", "drug")),
    ("veterinario", "Veterinário", ("pet", "animal", "vet", "veterin")),
    ("chef", "Chef", ("receita", "recipe", "cozinha", "kitchen", "comida", "food", "restaurante", "cardapio", "menu")),
    ("chef-2", "Chef confeiteira", ("confeit", "doce", "bolo", "padaria", "bakery")),
    ("chef-3", "Cozinheiro", ("delivery", "ifood", "lanchonete")),
    ("garconete", "Garçonete", ("pedido", "order", "reserva", "booking", "hospitalidade")),
    ("barista", "Barista", ("cafe", "coffee", "bar", "bebida", "drink")),
    ("agricultor", "Agricultor", ("agro", "fazenda", "farm", "colheita", "crop", "safra", "plantio")),
    ("fazendeiro", "Fazendeiro", ("pecuar", "gado", "cattle", "rancho")),
    ("fazendeira", "Fazendeira", ("sustentab", "ambiental", "clima", "climate", "esg")),
    ("pedreiro", "Mestre de obras", ("reforma", "reparo", "repair", "conserto")),
    ("soldado", "Soldado", ("defesa", "defense", "missao", "mission", "tatic", "tactic")),
    ("palhaco", "Palhaço", ("humor", "piada", "joke", "comedia", "comedy", "meme")),
    ("bobo-da-corte", "Bobo da corte", ("diversao", "fun", "entretenim", "entertain", "jogo", "game")),
    ("pirata-2", "Pirata capitã", ("tesouro", "treasure", "cacador", "hunt")),
    ("cowgirl", "Cowgirl", ("rodeio", "country")),
    ("rei", "Rei", ("rei", "king", "chefe")),
    ("rainha", "Rainha", ("rainha", "queen", "vip", "premium")),
    ("padre", "Padre", ("igreja", "church", "paroquia")),
    ("freira", "Freira", ("convento", "caridade", "charity", "ong", "voluntar")),
    ("cantora", "Apresentadora", ("cantor", "singer", "show", "festival", "evento", "event", "palestra",
                                  "social media", "rede social", "instagram", "linkedin", "post", "influenc",
                                  "marketing", "campanha", "campaign")),
    ("gestora", "Gestora de pessoas", ("rh", "hr", "recrut", "recruit", "pessoas", "people", "vaga", "candidat",
                                       "talento", "talent", "colaborador", "administrativ", "admin", "escritorio",
                                       "office", "documento", "document", "planilha", "spreadsheet", "relatorio",
                                       "report")),
]
IDS = [c[0] for c in CATALOG]
LABEL = {i: label for i, label, _ in CATALOG}
# sem palavra que case: um destes, sempre o mesmo para o mesmo nome (os personagens "de fantasia" só por escolha)
GENERIC = ("executivo", "consultora", "cientista-dados", "gerente-projetos", "gestora", "atendente",
           "desenvolvedor", "estudante", "concierge", "mentor", "designer", "suporte")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def _rx(kw: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(kw) + (r"\b" if len(kw) <= 3 else ""))


_RX = {i: [_rx(k) for k in kws] for i, _, kws in CATALOG}


def pick(name: str, objective: str = "") -> str:
    """O avatar pelo papel: as palavras do nome valem o dobro das do objetivo; empate fica com o primeiro do catálogo."""
    n, o = _norm(name), _norm(objective)
    best, score = "", 0
    for i in IDS:
        s = sum(2 * bool(r.search(n)) + bool(r.search(o)) for r in _RX[i])
        if s > score:
            best, score = i, s
    return best or GENERIC[zlib.crc32(n.encode()) % len(GENERIC)]


def check(avatar: str) -> str:
    if avatar not in LABEL:
        raise PlatformError(f"avatar '{avatar}' não existe; veja GET /api/avatars")
    return avatar


def of(a) -> str:
    """O avatar de um agente: o escolhido ou, nos antigos (sem escolha gravada), o do papel."""
    return a.avatar if a.avatar in LABEL else pick(a.name, a.objective)


def url(avatar: str | None) -> str:
    return f"/ui/avatars/{avatar}.webp" if avatar in LABEL else ""


def catalog() -> list[dict]:
    return [{"id": i, "label": label, "url": url(i), "roles": list(kws[:6])} for i, label, kws in CATALOG]
