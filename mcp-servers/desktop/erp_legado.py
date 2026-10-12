"""ERP legado de exemplo para o desktop governado: telas feitas em Tk, sem árvore de acessibilidade (como muito sistema
antigo de Windows), então só dá para operar olhando a tela.

- Login (usuário e senha; a senha de teste vem de /opt/erp/senha, gerada na imagem).
- Movimentos → Baixa de fatura: consulta, data e valor, e um diálogo "Confirma a baixa?" (Sim/Não). Cada baixa
  confirmada vira uma linha em ~/erp/baixas.csv — é o que os testes conferem.
- Relatórios → Faturas em aberto: uma janela à parte com a lista longa (precisa rolar).
"""
import csv
import os
import tkinter as tk
from datetime import datetime

CLIENTES = ["ACME Indústria Ltda.", "Beta Serviços S.A.", "Gama Comércio ME", "Delta Logística", "Épsilon Tecnologia",
            "Zeta Alimentos", "Eta Construções", "Teta Farmacêutica"]
FATURAS = {"88": ("ACME Indústria Ltda.", "2.500,00"), "91": ("Beta Serviços S.A.", "980,40"),
           "102": ("Gama Comércio ME", "12.000,00")}
for n in range(103, 141):  # a lista longa do relatório
    FATURAS[str(n)] = (CLIENTES[n % len(CLIENTES)], f"{(n * 37) % 9000 + 150:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
FATURAS["140"] = ("Teta Farmacêutica", "7.777,77")
OUT = os.path.expanduser("~/erp/baixas.csv")
try:
    SENHA = open("/opt/erp/senha", encoding="utf-8").read().strip()
except OSError:
    SENHA = "erp"
BG = "#d4d0c8"

root = tk.Tk()
root.title("ERP Legado 7.2 — Login")
root.geometry("760x460+60+60")
root.configure(bg=BG)


def clear():
    for w in root.winfo_children():
        if not isinstance(w, tk.Menu):
            w.destroy()


# ------------------------------------------------------------------ login
def tela_login(msg=""):
    clear()
    root.title("ERP Legado 7.2 — Login")
    root.config(menu=tk.Menu(root))
    tk.Label(root, text="Acesso ao sistema", font=("DejaVu Sans", 16, "bold"), bg=BG).place(x=24, y=16)
    tk.Label(root, text="Usuário:", bg=BG, font=("DejaVu Sans", 11)).place(x=180, y=120)
    u = tk.Entry(root, width=22, font=("DejaVu Sans", 12))
    u.place(x=300, y=118)
    tk.Label(root, text="Senha:", bg=BG, font=("DejaVu Sans", 11)).place(x=180, y=180)
    s = tk.Entry(root, width=22, font=("DejaVu Sans", 12), show="*")
    s.place(x=300, y=178)
    st = tk.Label(root, text=msg or "Informe usuário e senha.", bg="#ffffe1", font=("DejaVu Sans", 11), anchor="w", relief="sunken")
    st.place(x=0, y=430, width=760, height=30)

    def entrar():
        if u.get().strip() and s.get() == SENHA:
            tela_baixa(f"Bem-vindo, {u.get().strip()}.")
        else:
            st.config(text="Usuário ou senha inválidos.")
    tk.Button(root, text="Entrar", width=12, command=entrar).place(x=300, y=240)


# ------------------------------------------------------------------ baixa
def tela_baixa(msg="Pronto."):
    clear()
    root.title("ERP Legado 7.2 — Contas a Receber")
    menu = tk.Menu(root)
    mov = tk.Menu(menu, tearoff=0)
    mov.add_command(label="Baixa de fatura", command=lambda: tela_baixa())
    menu.add_cascade(label="Movimentos", menu=mov)
    rel = tk.Menu(menu, tearoff=0)
    rel.add_command(label="Faturas em aberto", command=relatorio)
    menu.add_cascade(label="Relatórios", menu=rel)
    root.config(menu=menu)
    tk.Label(root, text="Baixa de fatura", font=("DejaVu Sans", 16, "bold"), bg=BG).place(x=24, y=16)
    fields = {}
    for i, (key, label) in enumerate([("fatura", "Número da fatura:"), ("data", "Data do pagamento (dd/mm/aaaa):"),
                                      ("valor", "Valor pago (R$):")]):
        tk.Label(root, text=label, bg=BG, font=("DejaVu Sans", 11)).place(x=24, y=70 + i * 90)
        e = tk.Entry(root, width=22, font=("DejaVu Sans", 12))
        e.place(x=330, y=68 + i * 90)
        fields[key] = e
    cliente = tk.Label(root, text="", bg=BG, font=("DejaVu Sans", 11), fg="#003399")
    cliente.place(x=24, y=110)
    status = tk.Label(root, text=msg, bg="#ffffe1", font=("DejaVu Sans", 11), anchor="w", relief="sunken")
    status.place(x=0, y=430, width=760, height=30)

    def consultar():
        f = fields["fatura"].get().strip()
        if f in FATURAS:
            cliente.config(text=f"Cliente: {FATURAS[f][0]}   ·   Valor em aberto: R$ {FATURAS[f][1]}")
            status.config(text=f"Fatura {f} encontrada.")
        else:
            cliente.config(text="")
            status.config(text=f"ERRO: a fatura '{f}' não existe.")

    def gravar(f, d, v):
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        with open(OUT, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow([f, FATURAS[f][0], d, v, datetime.now().isoformat(timespec="seconds")])
        for e in fields.values():
            e.delete(0, "end")
        cliente.config(text="")
        status.config(text=f"Baixa registrada: fatura {f}, pago R$ {v} em {d}.")

    def confirmar():
        f, d, v = (fields[k].get().strip() for k in ("fatura", "data", "valor"))
        if f not in FATURAS:
            status.config(text="ERRO: consulte uma fatura válida antes de confirmar.")
            return
        try:
            datetime.strptime(d, "%d/%m/%Y")
        except ValueError:
            status.config(text="ERRO: data inválida — use o formato dd/mm/aaaa (ex.: 05/03/2026).")
            return
        if not v:
            status.config(text="ERRO: informe o valor pago.")
            return
        dlg = tk.Toplevel(root)
        dlg.title("Confirmação")
        dlg.geometry("420x170+230+220")
        dlg.configure(bg=BG)
        dlg.transient(root)
        tk.Label(dlg, text=f"Confirma a baixa da fatura {f}\n({FATURAS[f][0]}) no valor de R$ {v}?", bg=BG,
                 font=("DejaVu Sans", 11), justify="left").place(x=20, y=20)

        def sim():
            dlg.destroy()
            gravar(f, d, v)

        def nao():
            dlg.destroy()
            status.config(text="Baixa não confirmada.")
        tk.Button(dlg, text="Sim", width=10, command=sim).place(x=90, y=110)
        tk.Button(dlg, text="Não", width=10, command=nao).place(x=230, y=110)
        dlg.grab_set()

    def cancelar():
        for e in fields.values():
            e.delete(0, "end")
        cliente.config(text="")
        status.config(text="Operação cancelada.")

    tk.Button(root, text="Consultar", width=12, command=consultar).place(x=600, y=64)
    tk.Button(root, text="Confirmar baixa", width=16, bg="#2b579a", fg="white", command=confirmar).place(x=330, y=350)
    tk.Button(root, text="Cancelar", width=12, command=cancelar).place(x=530, y=350)


# ------------------------------------------------------------------ relatório
def relatorio():
    win = tk.Toplevel(root)
    win.title("Relatório — Faturas em aberto")
    win.geometry("640x420+140+120")
    win.configure(bg=BG)
    tk.Label(win, text="Fatura   Cliente                         Valor (R$)", font=("DejaVu Sans Mono", 11, "bold"),
             bg=BG, anchor="w").place(x=10, y=10, width=600)
    lb = tk.Listbox(win, font=("DejaVu Sans Mono", 11), height=16)
    sb = tk.Scrollbar(win, command=lb.yview)
    lb.config(yscrollcommand=sb.set)
    lb.place(x=10, y=40, width=590, height=330)
    sb.place(x=600, y=40, width=20, height=330)
    for f, (c, v) in sorted(FATURAS.items(), key=lambda x: int(x[0])):
        lb.insert("end", f"{f:<8} {c:<30} {v:>12}")
    tk.Button(win, text="Fechar", width=10, command=win.destroy).place(x=520, y=380)


tela_login()
root.mainloop()
