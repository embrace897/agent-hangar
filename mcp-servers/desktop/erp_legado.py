"""ERP legado de exemplo para o desktop governado: uma tela de baixa de fatura feita em Tk, sem árvore de
acessibilidade (como muito sistema antigo de Windows), então só dá para operar olhando a tela. Cada baixa confirmada
vira uma linha em ~/erp/baixas.csv — é o que os testes conferem."""
import csv
import os
import tkinter as tk
from datetime import datetime

FATURAS = {"88": ("ACME Indústria Ltda.", "2.500,00"), "91": ("Beta Serviços S.A.", "980,40"),
           "102": ("Gama Comércio ME", "12.000,00")}
OUT = os.path.expanduser("~/erp/baixas.csv")

root = tk.Tk()
root.title("ERP Legado 7.2 — Contas a Receber")
root.geometry("760x460+60+60")
root.configure(bg="#d4d0c8")
tk.Label(root, text="Baixa de fatura", font=("DejaVu Sans", 16, "bold"), bg="#d4d0c8").place(x=24, y=16)

fields = {}
for i, (key, label) in enumerate([("fatura", "Número da fatura:"), ("data", "Data do pagamento (dd/mm/aaaa):"),
                                  ("valor", "Valor pago (R$):")]):
    tk.Label(root, text=label, bg="#d4d0c8", font=("DejaVu Sans", 11)).place(x=24, y=70 + i * 90)
    e = tk.Entry(root, width=22, font=("DejaVu Sans", 12))
    e.place(x=330, y=68 + i * 90)
    fields[key] = e
    if key == "fatura":
        cliente = tk.Label(root, text="", bg="#d4d0c8", font=("DejaVu Sans", 11), fg="#003399")
        cliente.place(x=24, y=110)

status = tk.Label(root, text="Pronto.", bg="#ffffe1", font=("DejaVu Sans", 11), anchor="w", relief="sunken")
status.place(x=0, y=430, width=760, height=30)


def consultar():
    f = fields["fatura"].get().strip()
    if f in FATURAS:
        cliente.config(text=f"Cliente: {FATURAS[f][0]}   ·   Valor em aberto: R$ {FATURAS[f][1]}")
        status.config(text=f"Fatura {f} encontrada.")
    else:
        cliente.config(text="")
        status.config(text=f"Fatura '{f}' não existe.")


def confirmar():
    f, d, v = (fields[k].get().strip() for k in ("fatura", "data", "valor"))
    if f not in FATURAS:
        status.config(text="Consulte uma fatura válida antes de confirmar.")
        return
    try:
        datetime.strptime(d, "%d/%m/%Y")
    except ValueError:
        status.config(text="Data inválida: use dd/mm/aaaa.")
        return
    if not v:
        status.config(text="Informe o valor pago.")
        return
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([f, FATURAS[f][0], d, v, datetime.now().isoformat(timespec="seconds")])
    status.config(text=f"Baixa registrada: fatura {f}, pago R$ {v} em {d}.")
    for e in fields.values():
        e.delete(0, "end")
    cliente.config(text="")


def cancelar():
    for e in fields.values():
        e.delete(0, "end")
    cliente.config(text="")
    status.config(text="Operação cancelada.")


tk.Button(root, text="Consultar", width=12, command=consultar).place(x=600, y=64)
tk.Button(root, text="Confirmar baixa", width=16, bg="#2b579a", fg="white", command=confirmar).place(x=330, y=350)
tk.Button(root, text="Cancelar", width=12, command=cancelar).place(x=530, y=350)
root.mainloop()
