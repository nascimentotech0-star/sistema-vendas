"""
Script de dados de teste — Nascimento Tech
Execute: python seed_test.py
"""
import random
from datetime import datetime, date, timedelta
from app import create_app
from models import db, User, Client, Sale, Renewal, ClientContact, PAYMENT_METHODS

app = create_app()

PLANS = ['Plano Basic', 'Plano Premium', 'Plano Pro', 'Plano Família', 'Plano Empresarial']
PAYMENT_KEYS = list(PAYMENT_METHODS.keys())

CLIENTS_DATA = [
    'Ana Souza', 'Carlos Lima', 'Fernanda Rocha', 'João Mendes',
    'Patrícia Nunes', 'Rafael Costa', 'Juliana Martins', 'Marcos Alves',
    'Beatriz Santos', 'Thiago Oliveira', 'Camila Ferreira', 'Lucas Pereira',
    'Amanda Silva', 'Diego Carvalho', 'Larissa Gomes',
]

ATTENDANTS_DATA = [
    ('ana.atendente', 'Ana Atendente', '123456'),
    ('pedro.atendente', 'Pedro Atendente', '123456'),
]


def random_amount():
    return round(random.choice([49.90, 79.90, 99.90, 149.90, 199.90, 299.90]), 2)


def seed():
    with app.app_context():
        print("Iniciando seed de dados de teste...")

        # ── Atendentes ────────────────────────────────────────────────────────
        attendants = []
        for username, name, pwd in ATTENDANTS_DATA:
            u = User.query.filter_by(username=username).first()
            if not u:
                u = User(username=username, name=name, role='attendant', is_active=True)
                u.set_password(pwd)
                db.session.add(u)
                print(f"  Atendente criado: {name} (usuário: {username} / senha: {pwd})")
            attendants.append(u)
        db.session.flush()

        # ── Clientes ──────────────────────────────────────────────────────────
        admin = User.query.filter_by(role='admin').first()
        phones = [
            '(11) 91234-5678', '(11) 98765-4321', '(21) 99988-7766',
            '(21) 91122-3344', '(31) 98877-6655', '(31) 97766-5544',
            '(41) 99955-4433', '(41) 98844-3322', '(51) 97733-2211',
            '(51) 96622-1100', '(61) 95511-0099', '(61) 94400-9988',
            '(71) 93399-8877', '(71) 92288-7766', '(81) 91177-6655',
        ]
        clients = []
        for i, cname in enumerate(CLIENTS_DATA):
            c = Client.query.filter_by(name=cname).first()
            if not c:
                c = Client(
                    name=cname,
                    phone=phones[i],
                    whatsapp=phones[i],
                    registered_by=admin.id,
                )
                db.session.add(c)
            clients.append(c)
        db.session.flush()

        # ── Vendas (últimos 60 dias) ───────────────────────────────────────────
        today = date.today()
        sales_created = 0

        for days_ago in range(60, -1, -1):
            day = today - timedelta(days=days_ago)

            # Pula alguns dias aleatoriamente para parecer real
            if random.random() < 0.15:
                continue

            # Mais vendas nos fins de semana e últimos dias
            n_sales = random.randint(1, 6)
            if day.weekday() >= 4:   # Sex/Sab/Dom
                n_sales = random.randint(3, 10)
            if days_ago <= 7:        # Última semana — mais movimento
                n_sales = random.randint(4, 12)

            for _ in range(n_sales):
                hour = random.randint(8, 21)
                minute = random.randint(0, 59)
                created_at = datetime(day.year, day.month, day.day, hour, minute)
                att = random.choice(attendants)
                client = random.choice(clients)
                amount = random_amount()
                rate = 5.0
                commission = round(amount * rate / 100, 2)

                sale = Sale(
                    attendant_id=att.id,
                    client_id=client.id,
                    amount=amount,
                    payment_method=random.choice(PAYMENT_KEYS),
                    commission_rate=rate,
                    commission_amount=commission,
                    is_overtime=False,
                    created_at=created_at,
                )
                db.session.add(sale)
                sales_created += 1

        print(f"  {sales_created} vendas criadas nos últimos 60 dias")

        # ── Renovações do mês atual ────────────────────────────────────────────
        import calendar
        first = date(today.year, today.month, 1)
        last = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])

        renewal_statuses = ['renewed', 'renewed', 'renewed', 'pending', 'pending', 'cancelled']
        renewals_created = 0

        for i, client in enumerate(clients):
            due = first + timedelta(days=random.randint(0, (last - first).days))
            status = random.choice(renewal_statuses)
            renewed_at = None
            if status == 'renewed':
                renewed_at = datetime(due.year, due.month, due.day,
                                      random.randint(9, 18), random.randint(0, 59))

            r = Renewal(
                client_id=client.id,
                plan_name=random.choice(PLANS),
                amount=random_amount(),
                due_date=due,
                status=status,
                renewed_at=renewed_at,
                attendant_id=random.choice(attendants).id,
                notes='Renovação automática de teste.' if i % 3 == 0 else None,
            )
            db.session.add(r)
            renewals_created += 1

        # Renovações mês anterior (já encerrado)
        if today.month == 1:
            prev_year, prev_month = today.year - 1, 12
        else:
            prev_year, prev_month = today.year, today.month - 1

        prev_first = date(prev_year, prev_month, 1)
        prev_last = date(prev_year, prev_month, calendar.monthrange(prev_year, prev_month)[1])

        for client in random.sample(clients, 10):
            due = prev_first + timedelta(days=random.randint(0, (prev_last - prev_first).days))
            status = random.choices(['renewed', 'cancelled'], weights=[80, 20])[0]
            renewed_at = datetime(due.year, due.month, due.day,
                                  random.randint(9, 18), 0) if status == 'renewed' else None
            r = Renewal(
                client_id=client.id,
                plan_name=random.choice(PLANS),
                amount=random_amount(),
                due_date=due,
                status=status,
                renewed_at=renewed_at,
                attendant_id=random.choice(attendants).id,
            )
            db.session.add(r)
            renewals_created += 1

        print(f"  {renewals_created} renovações criadas")

        # ── Contatos (histórico de atendimentos) ──────────────────────────────
        CHANNELS   = ['whatsapp', 'whatsapp', 'whatsapp', 'phone', 'email']
        DIRECTIONS = ['incoming', 'incoming', 'outgoing']
        NOTES_IN = [
            'Cliente perguntou sobre renovação do plano.',
            'Dúvida sobre cobrança da fatura.',
            'Solicitou upgrade de plano.',
            'Informou que vai indicar um amigo.',
            'Perguntou sobre desconto para pagamento anual.',
            'Reclamou de instabilidade no serviço.',
            'Confirmou recebimento do comprovante.',
            None, None,
        ]
        NOTES_OUT = [
            'Tentativa de renovação — cliente aceitou.',
            'Lembrete de vencimento enviado.',
            'Oferta de upgrade apresentada.',
            'Cobrança de mensalidade realizada.',
            'Follow-up pós-venda.',
            None, None,
        ]

        contacts_created = 0

        # 10 clientes com histórico recente (OK)
        for c in clients[:10]:
            n = random.randint(2, 6)
            for i in range(n):
                days_ago = random.randint(0, 4)
                hour = random.randint(8, 20)
                direction = random.choice(DIRECTIONS)
                contact = ClientContact(
                    client_id=c.id,
                    attendant_id=random.choice(attendants).id,
                    contacted_at=datetime(today.year, today.month, today.day, hour, random.randint(0,59)) - timedelta(days=days_ago),
                    direction=direction,
                    channel=random.choice(CHANNELS),
                    notes=random.choice(NOTES_IN if direction == 'incoming' else NOTES_OUT),
                )
                db.session.add(contact)
                contacts_created += 1

        # 3 clientes sem contato há 6-10 dias (em risco)
        for c in clients[10:13]:
            days_ago = random.randint(6, 10)
            contact = ClientContact(
                client_id=c.id,
                attendant_id=random.choice(attendants).id,
                contacted_at=datetime.now() - timedelta(days=days_ago),
                direction='incoming',
                channel='whatsapp',
                notes='Último contato registrado.',
            )
            db.session.add(contact)
            contacts_created += 1

        # 2 clientes sem NENHUM contato (nunca foram atendidos)
        # clients[13] e clients[14] ficam sem contato propositalmente

        print(f"  {contacts_created} contatos criados ({len(clients)-2-3} clientes OK, 3 em risco, 2 sem contato)")

        db.session.commit()
        print("\nDados de teste inseridos com sucesso!")
        print("=" * 45)
        print("Acesse com:")
        print("  Admin   → usuário: admin       | senha: admin123")
        for username, name, pwd in ATTENDANTS_DATA:
            print(f"  Atend.  → usuário: {username:<18} | senha: {pwd}")
        print("=" * 45)


if __name__ == '__main__':
    seed()
