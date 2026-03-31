"""
reset_data.py — Apaga todos os registros de teste.
Preserva APENAS o usuário admin.

Uso:
    python reset_data.py
"""
import sys
from app import create_app
from models import (db, User, Attendance, AttendanceBreak, OvertimeRequest,
                    Client, ClientContact, Sale, Renewal, Message,
                    AttendantGoal, CommissionPayment, PriceItem)

app = create_app()

with app.app_context():
    print("=" * 50)
    print("RESET DE DADOS DE TESTE")
    print("=" * 50)
    print()

    # Contagem antes
    counts = {
        'Vendas':           Sale.query.count(),
        'Renovações':       Renewal.query.count(),
        'Clientes':         Client.query.count(),
        'Contatos':         ClientContact.query.count(),
        'Mensagens':        Message.query.count(),
        'Pontos':           Attendance.query.count(),
        'Pausas':           AttendanceBreak.query.count(),
        'Horas extras':     OvertimeRequest.query.count(),
        'Metas':            AttendantGoal.query.count(),
        'Comissões pagas':  CommissionPayment.query.count(),
        'Tabela de preços': PriceItem.query.count(),
        'Usuários (não-admin)': User.query.filter(User.role != 'admin').count(),
    }

    print("Registros encontrados:")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    print()

    resposta = input("Confirma apagar TUDO acima? Digite 'SIM' para continuar: ").strip()
    if resposta != 'SIM':
        print("Cancelado.")
        sys.exit(0)

    print()
    print("Apagando...")

    # Ordem importa por causa das foreign keys
    CommissionPayment.query.delete()
    AttendantGoal.query.delete()
    Message.query.delete()
    ClientContact.query.delete()
    Sale.query.delete()
    Renewal.query.delete()
    Client.query.delete()
    AttendanceBreak.query.delete()
    OvertimeRequest.query.delete()
    Attendance.query.delete()
    PriceItem.query.delete()

    # Remove usuários não-admin
    User.query.filter(User.role != 'admin').delete()

    db.session.commit()

    print()
    print("✓ Todos os registros de teste foram apagados.")
    print("✓ Usuário admin preservado.")
    print()

    # Confirmação final
    admin = User.query.filter_by(role='admin').first()
    if admin:
        print(f"  Admin: {admin.username} — {admin.name}")
    print("=" * 50)
