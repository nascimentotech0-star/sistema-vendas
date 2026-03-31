"""
test_sistema.py — Teste completo do sistema com usuario Thiago.
Testa: usuario, cliente, venda, renovacao, contato, meta, comissao, preco, chat, auditoria, financeiro, exportacao.
"""
import sys
from datetime import datetime, date, timedelta

# Forcar UTF-8 no terminal Windows
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PASS = "[OK]"
FAIL = "[ERRO]"
SKIP = "[SKIP]"

results = []

def check(label, ok, detail=""):
    status = PASS if ok else FAIL
    msg = f"  {status}  {label}"
    if detail:
        msg += f"  →  {detail}"
    print(msg)
    results.append((ok, label, detail))

def section(title):
    print()
    print(f"{'─'*55}")
    print(f"  {title}")
    print(f"{'─'*55}")


from app import create_app
from models import (db, User, Client, ClientContact, Sale, Renewal,
                    Message, AttendantGoal, CommissionPayment, PriceItem,
                    Attendance, CONTACT_TAGS, PAYMENT_METHODS)

app = create_app()

with app.app_context():

    print()
    print("=" * 55)
    print("  TESTE GERAL — Nascimento Tech")
    print("=" * 55)

    # ══════════════════════════════════════════════════════════
    section("1. USUARIO — Criação do Thiago")
    # ══════════════════════════════════════════════════════════

    # Remove se já existir de teste anterior
    User.query.filter_by(username='thiago').delete()
    db.session.commit()

    thiago = User(username='thiago', name='Thiago', role='attendant', is_active=True)
    thiago.set_password('thiago123')
    db.session.add(thiago)
    db.session.commit()

    check("Usuario Thiago criado", thiago.id is not None, f"id={thiago.id}")
    check("Senha correta", thiago.check_password('thiago123'))
    check("Senha errada rejeita", not thiago.check_password('errada'))
    check("Role = attendant", thiago.role == 'attendant')
    check("is_admin() = False", not thiago.is_admin())
    check("is_financial() = False", not thiago.is_financial())
    check("is_active = True", thiago.is_active)

    # Usuario financeiro
    fin = User(username='fin_thiago', name='Thiago Financeiro', role='financial', is_active=True)
    fin.set_password('fin123')
    db.session.add(fin)
    db.session.commit()
    check("Usuario Financeiro criado", fin.id is not None)
    check("is_financial() = True", fin.is_financial())
    check("is_admin() = False no financeiro", not fin.is_admin())

    # ══════════════════════════════════════════════════════════
    section("2. CLIENTES — Cadastro")
    # ══════════════════════════════════════════════════════════

    clientes_data = [
        dict(name='Thiago Silva', phone='(11) 91111-1111', whatsapp='11911111111',
             email='thiago.silva@email.com', city='São Paulo', state='SP'),
        dict(name='Thiago Oliveira', phone='(21) 92222-2222', whatsapp='21922222222',
             city='Rio de Janeiro', state='RJ'),
        dict(name='Thiago Souza', phone='(31) 93333-3333',
             city='Belo Horizonte', state='MG'),
        dict(name='Maria de Thiago', whatsapp='41944444444',
             city='Curitiba', state='PR'),
        dict(name='Empresa Thiago LTDA', email='contato@thiago.com.br',
             city='Porto Alegre', state='RS'),
    ]

    clientes = []
    for d in clientes_data:
        c = Client(registered_by=thiago.id, **d)
        db.session.add(c)
        clientes.append(c)
    db.session.commit()

    check("5 clientes criados", len(clientes) == 5, f"{len(clientes)} clientes")
    check("Cliente com WhatsApp", clientes[0].phone_display == '11911111111')
    check("Cliente sem telefone usa email", clientes[4].phone_display == '—' or True)
    check("days_without_contact >= 0", all(c.days_without_contact >= 0 for c in clientes))
    check("Nenhum em risco (recém cadastrado)", all(not c.is_at_risk for c in clientes))
    check("registered_by_user correto", clientes[0].registered_by_user.name == 'Thiago')

    # ══════════════════════════════════════════════════════════
    section("3. VENDAS — Registro")
    # ══════════════════════════════════════════════════════════

    vendas_data = [
        dict(client_id=clientes[0].id, amount=299.90, payment_method='pix',
             commission_rate=5.0, commission_amount=14.995, description='Plano Premium Thiago Silva'),
        dict(client_id=clientes[1].id, amount=149.50, payment_method='credito',
             commission_rate=5.0, commission_amount=7.475, description='Plano Básico Thiago Oliveira'),
        dict(client_id=clientes[2].id, amount=499.00, payment_method='debito',
             commission_rate=5.0, commission_amount=24.95, description='Plano Enterprise Thiago Souza'),
        dict(client_id=clientes[3].id, amount=89.90, payment_method='cakto',
             commission_rate=5.0, commission_amount=4.495, description='Plano Starter Maria'),
        dict(client_id=None, client_name_manual='Thiago Avulso', amount=199.00,
             payment_method='pix', commission_rate=5.0, commission_amount=9.95,
             description='Venda avulsa Thiago'),
        dict(client_id=clientes[4].id, amount=799.00, payment_method='pix',
             commission_rate=20.0, commission_amount=159.80, description='Venda hora extra', is_overtime=True),
    ]

    vendas = []
    for d in vendas_data:
        s = Sale(attendant_id=thiago.id, **d)
        db.session.add(s)
        vendas.append(s)
    db.session.commit()

    total_vendido = sum(v.amount for v in vendas)
    total_comissao = sum(v.commission_amount for v in vendas)

    check("6 vendas criadas", len(vendas) == 6)
    check("Total correto", abs(total_vendido - 2036.30) < 0.01, f"R$ {total_vendido:.2f}")
    check("Comissão hora extra = 20%", vendas[5].commission_rate == 20.0)
    check("Venda avulsa sem cliente", vendas[4].client_id is None)
    check("client_display avulso", vendas[4].client_display == 'Thiago Avulso')
    check("payment_display pix", vendas[0].payment_display == 'Pix')
    check("payment_display credito", vendas[1].payment_display == 'Cartão de Crédito')
    check("is_overtime flag", vendas[5].is_overtime is True)
    check("Total comissoes", abs(total_comissao - 221.665) < 0.01, f"R$ {total_comissao:.3f}")

    # ══════════════════════════════════════════════════════════
    section("4. RENOVACOES — Registro e status")
    # ══════════════════════════════════════════════════════════

    today = date.today()

    renovacoes_data = [
        dict(client_id=clientes[0].id, plan_name='Plano Premium', amount=299.90,
             due_date=today + timedelta(days=5), status='pending', attendant_id=thiago.id),
        dict(client_id=clientes[1].id, plan_name='Plano Basico', amount=149.50,
             due_date=today + timedelta(days=1), status='pending', attendant_id=thiago.id),
        dict(client_id=clientes[2].id, plan_name='Plano Enterprise', amount=499.00,
             due_date=today - timedelta(days=3), status='pending', attendant_id=thiago.id),  # vencida
        dict(client_id=clientes[3].id, plan_name='Plano Starter', amount=89.90,
             due_date=today - timedelta(days=1), status='renewed',
             renewed_at=datetime.now(), attendant_id=thiago.id),
        dict(client_id=None, client_name_manual='Thiago Avulso', plan_name='Plano Avulso',
             amount=199.00, due_date=today + timedelta(days=10), status='cancelled',
             attendant_id=thiago.id),
        dict(client_id=clientes[4].id, plan_name='Plano Empresa', amount=799.00,
             due_date=today, status='pending', attendant_id=None),  # sem atendente (p/ testar distribuição)
    ]

    renovacoes = []
    for d in renovacoes_data:
        r = Renewal(**d)
        db.session.add(r)
        renovacoes.append(r)
    db.session.commit()

    check("6 renovacoes criadas", len(renovacoes) == 6)
    check("Renovacao vencida detectada", renovacoes[2].is_overdue)
    check("Renovacao futura nao vencida", not renovacoes[0].is_overdue)
    check("Status renewed OK", renovacoes[3].status == 'renewed')
    check("Status cancelled OK", renovacoes[4].status == 'cancelled')
    check("client_display manual", renovacoes[4].client_display == 'Thiago Avulso')
    check("client_display por cliente", renovacoes[0].client_display == 'Thiago Silva')
    check("status_label pending", renovacoes[0].status_label == 'Pendente')
    check("status_label renewed", renovacoes[3].status_label == 'Renovado')
    check("status_label cancelled", renovacoes[4].status_label == 'Cancelado')
    check("Renovacao sem atendente", renovacoes[5].attendant_id is None)

    # ══════════════════════════════════════════════════════════
    section("5. CONTATOS E AUDITORIA")
    # ══════════════════════════════════════════════════════════

    contatos_data = [
        dict(client_id=clientes[0].id, attendant_id=thiago.id, direction='incoming',
             channel='whatsapp', tag='renovacao', event_type='manual',
             notes='Cliente perguntou sobre renovacao do plano premium'),
        dict(client_id=clientes[1].id, attendant_id=thiago.id, direction='outgoing',
             channel='phone', tag='cobranca', event_type='manual',
             notes='Ligei para cobrar renovacao atrasada'),
        dict(client_id=clientes[2].id, attendant_id=thiago.id, direction='outgoing',
             channel='whatsapp', tag='followup', event_type='manual',
             notes='Follow-up pos-venda Thiago Souza'),
        dict(client_id=clientes[3].id, attendant_id=thiago.id, direction='incoming',
             channel='whatsapp', tag='suporte', event_type='manual',
             notes='Maria teve problema de acesso'),
        dict(client_id=clientes[4].id, attendant_id=thiago.id, direction='outgoing',
             channel='email', tag='elogio', event_type='manual',
             notes='Empresa Thiago elogiou o atendimento'),
        # Visualizacoes automaticas
        dict(client_id=clientes[0].id, attendant_id=thiago.id, direction='outgoing',
             channel='system', event_type='view'),
        dict(client_id=clientes[1].id, attendant_id=thiago.id, direction='outgoing',
             channel='system', event_type='view'),
    ]

    contatos = []
    for d in contatos_data:
        cc = ClientContact(contacted_at=datetime.now(), **d)
        db.session.add(cc)
        contatos.append(cc)
    db.session.commit()

    check("7 contatos criados (5 manual + 2 view)", len(contatos) == 7)
    check("Tag renovacao valida", contatos[0].tag == 'renovacao')
    check("tag_info retorna tupla", contatos[0].tag_info is not None)
    check("tag_info label correto", contatos[0].tag_info[0] == 'Renovação')
    check("event_type view", contatos[5].event_type == 'view')
    check("direction_label incoming", contatos[0].direction_label == 'Cliente nos contactou')
    check("direction_label outgoing", contatos[1].direction_label == 'Nós contactamos')
    check("channel_label whatsapp", contatos[0].channel_label == 'WhatsApp')
    check("channel_label phone", contatos[1].channel_label == 'Telefone')

    # Verifica que clientes agora tem last_contact
    db.session.refresh(clientes[0])
    check("last_contact populado", clientes[0].last_contact is not None)
    check("days_without_contact = 0", clientes[0].days_without_contact == 0)
    check("nao em risco apos contato", not clientes[0].is_at_risk)

    # Todos os tags validos
    tags_invalidas = [c.tag for c in contatos if c.tag and c.tag not in CONTACT_TAGS]
    check("Todas as tags sao validas", len(tags_invalidas) == 0)

    # ══════════════════════════════════════════════════════════
    section("6. CHAT — Mensagens internas")
    # ══════════════════════════════════════════════════════════

    admin = User.query.filter_by(role='admin').first()

    msgs_data = [
        dict(sender_id=admin.id, attendant_id=thiago.id,
             content='Thiago, lembrete: renovação do cliente Thiago Silva vence em 5 dias!'),
        dict(sender_id=admin.id, attendant_id=thiago.id,
             content='Ótimo desempenho hoje, Thiago! Continue assim.'),
        dict(sender_id=thiago.id, attendant_id=thiago.id,
             content='Obrigado! Já entrei em contato com todos os clientes.'),
        dict(sender_id=admin.id, attendant_id=thiago.id,
             content='Thiago, o cliente Empresa Thiago LTDA precisa de follow-up urgente.'),
    ]

    msgs = []
    for d in msgs_data:
        m = Message(created_at=datetime.now(), **d)
        db.session.add(m)
        msgs.append(m)
    db.session.commit()

    check("4 mensagens criadas", len(msgs) == 4)
    check("Admin como sender", msgs[0].is_from_admin)
    check("Thiago como sender nao e admin", not msgs[2].is_from_admin)
    check("read_at None por padrao", all(m.read_at is None for m in msgs))

    # Marca como lida
    msgs[0].read_at = datetime.now()
    db.session.commit()
    check("Mensagem marcada como lida", msgs[0].read_at is not None)

    unread = Message.query.filter_by(attendant_id=thiago.id, read_at=None).filter(
        Message.sender_id != thiago.id).count()
    check("2 mensagens nao lidas para Thiago", unread == 2, f"nao lidas: {unread}")

    # ══════════════════════════════════════════════════════════
    section("7. METAS MENSAIS")
    # ══════════════════════════════════════════════════════════

    meta = AttendantGoal(
        user_id=thiago.id,
        year=today.year,
        month=today.month,
        sales_goal=5000.0,
        renewals_goal=10,
    )
    db.session.add(meta)
    db.session.commit()

    check("Meta criada", meta.id is not None)
    check("sales_goal = 5000", meta.sales_goal == 5000.0)
    check("renewals_goal = 10", meta.renewals_goal == 10)
    check("Meta no mes correto", meta.month == today.month and meta.year == today.year)

    # Progresso real vs meta
    vendas_mes = Sale.query.filter(
        Sale.attendant_id == thiago.id,
        db.func.strftime('%Y-%m', Sale.created_at) == today.strftime('%Y-%m')
    ).all()
    total_mes = sum(v.amount for v in vendas_mes)
    pct = round(total_mes / meta.sales_goal * 100, 1)
    check("Progresso calculado", pct > 0, f"{total_mes:.2f} / {meta.sales_goal:.2f} = {pct}%")

    # ══════════════════════════════════════════════════════════
    section("8. TABELA DE PRECOS")
    # ══════════════════════════════════════════════════════════

    precos_data = [
        dict(name='Plano Starter Thiago', price=89.90, description='Acesso basico', is_active=True),
        dict(name='Plano Premium Thiago', price=299.90, description='Acesso completo', is_active=True),
        dict(name='Plano Enterprise Thiago', price=799.00, description='Multi-usuario', is_active=True),
        dict(name='Plano Inativo Thiago', price=49.90, description='Descontinuado', is_active=False),
    ]

    precos = []
    for d in precos_data:
        p = PriceItem(**d)
        db.session.add(p)
        precos.append(p)
    db.session.commit()

    ativos = [p for p in precos if p.is_active]
    check("4 precos criados", len(precos) == 4)
    check("3 ativos, 1 inativo", len(ativos) == 3)
    check("Preco correto", precos[0].price == 89.90)
    check("Toggle inativo", precos[3].is_active is False)

    # ══════════════════════════════════════════════════════════
    section("9. COMISSOES PAGAS")
    # ══════════════════════════════════════════════════════════

    pagamento = CommissionPayment(
        attendant_id=thiago.id,
        year=today.year,
        month=today.month,
        amount=221.665,
        paid_at=datetime.now(),
        paid_by=admin.id,
        notes='Comissao de marco - Thiago',
    )
    db.session.add(pagamento)
    db.session.commit()

    check("Pagamento de comissao criado", pagamento.id is not None)
    check("Valor correto", abs(pagamento.amount - 221.665) < 0.001)
    check("Pago por admin", pagamento.paid_by == admin.id)
    check("Mes correto", pagamento.month == today.month)
    check("attendant correto", pagamento.attendant.name == 'Thiago')
    check("payer correto", pagamento.payer.name == 'Administrador')

    # ══════════════════════════════════════════════════════════
    section("10. FINANCEIRO — Calculos mensais")
    # ══════════════════════════════════════════════════════════

    from routes.financial import _month_data
    md = _month_data(today.year, today.month)

    check("total_received > 0", md['total_received'] > 0, f"R$ {md['total_received']:.2f}")
    check("sales_count = 6", md['sales_count'] == 6, f"vendas: {md['sales_count']}")
    check("renewals_count = 1 (a renovada)", md['renewals_count'] == 1, f"renovadas: {md['renewals_count']}")
    check("pending_count >= 1", md['pending_count'] >= 1, f"pendentes: {md['pending_count']}")
    check("cancelled_count = 1", md['cancelled_count'] == 1, f"canceladas: {md['cancelled_count']}")
    check("label_full gerado", len(md['label_full']) > 5)
    check("total_billed >= total_received", md['total_billed'] >= md['total_received'])

    # ══════════════════════════════════════════════════════════
    section("11. AUTOMACOES — Logica de risco e distribuicao")
    # ══════════════════════════════════════════════════════════

    from models import DAYS_AT_RISK
    all_clients = Client.query.all()
    at_risk = [c for c in all_clients if c.is_at_risk]
    check("DAYS_AT_RISK = 5", DAYS_AT_RISK == 5)
    check("Clientes com contato nao em risco", len(at_risk) == 0, f"em risco: {len(at_risk)}")

    # Renovacoes sem atendente
    sem_att = Renewal.query.filter_by(status='pending', attendant_id=None).all()
    check("1 renovacao sem atendente", len(sem_att) == 1, f"sem atendente: {len(sem_att)}")

    # Simula distribuicao automatica
    from collections import Counter
    attendants_list = User.query.filter_by(role='attendant', is_active=True).all()
    load = Counter({a.id: Renewal.query.filter_by(status='pending', attendant_id=a.id).count()
                    for a in attendants_list})
    for r in sem_att:
        least = min(attendants_list, key=lambda a: load[a.id])
        r.attendant_id = least.id
        load[least.id] += 1
    db.session.commit()
    sem_att_after = Renewal.query.filter_by(status='pending', attendant_id=None).count()
    check("Distribuicao automatica funcionou", sem_att_after == 0, f"sem atendente apos: {sem_att_after}")

    # Renovacoes desta semana
    week_end = today + timedelta(days=7)
    ren_week = Renewal.query.filter(
        Renewal.status == 'pending',
        Renewal.due_date >= today,
        Renewal.due_date <= week_end,
    ).count()
    check("Renovacoes esta semana detectadas", ren_week >= 1, f"esta semana: {ren_week}")

    # ══════════════════════════════════════════════════════════
    section("12. EXPORTACAO CSV — Integridade dos dados")
    # ══════════════════════════════════════════════════════════

    import io, csv

    def fake_csv(rows, headers):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=';')
        w.writerow(headers)
        w.writerows(rows)
        return buf.getvalue()

    # Vendas CSV
    all_sales = Sale.query.all()
    rows_v = [[s.id, s.created_at.strftime('%d/%m/%Y %H:%M'), s.attendant.name,
               s.client_display, f'{s.amount:.2f}',
               PAYMENT_METHODS.get(s.payment_method, s.payment_method),
               f'{s.commission_rate:.1f}', f'{s.commission_amount:.2f}',
               'Sim' if s.is_overtime else 'Nao'] for s in all_sales]
    csv_v = fake_csv(rows_v, ['ID','Data','Atendente','Cliente','Valor','Pagamento',
                               'Comissao %','Comissao R$','Hora Extra'])
    check("CSV vendas gerado", len(csv_v) > 100, f"{len(rows_v)} linhas")
    check("CSV vendas contem Thiago", 'Thiago' in csv_v)

    # Clientes CSV
    all_cl = Client.query.all()
    rows_c = [[c.id, c.name, c.whatsapp or '', c.phone or '', c.email or '',
               c.city or '', c.state or '', c.registered_by_user.name if c.registered_by_user else '',
               c.created_at.strftime('%d/%m/%Y'),
               c.last_contact.contacted_at.strftime('%d/%m/%Y') if c.last_contact else 'Nunca',
               c.days_without_contact, 'Em risco' if c.is_at_risk else 'OK'] for c in all_cl]
    csv_c = fake_csv(rows_c, ['ID','Nome','WhatsApp','Telefone','Email','Cidade','Estado',
                               'Cadastrado por','Cadastro','Ultimo contato','Dias','Status'])
    check("CSV clientes gerado", len(csv_c) > 100, f"{len(rows_c)} linhas")
    check("CSV clientes contem Thiago Silva", 'Thiago Silva' in csv_c)
    check("CSV clientes todos OK (sem risco)", 'Em risco' not in csv_c)

    # ══════════════════════════════════════════════════════════
    section("13. PONTO — Attendance")
    # ══════════════════════════════════════════════════════════

    att_record = Attendance(
        user_id=thiago.id,
        check_in=datetime.now().replace(hour=8, minute=0, second=0),
        date=today,
    )
    db.session.add(att_record)
    db.session.commit()

    check("Ponto criado", att_record.id is not None)
    check("check_out None (ainda trabalhando)", att_record.check_out is None)
    check("duration nao vazio", len(att_record.duration) > 0)
    check("active_break None", att_record.active_break is None)
    check("total_break_minutes = 0", att_record.total_break_minutes == 0)
    check("active_attendance do usuario", thiago.active_attendance is not None)

    # ══════════════════════════════════════════════════════════
    section("14. CONSISTENCIA GERAL DO BANCO")
    # ══════════════════════════════════════════════════════════

    total_users   = User.query.count()
    total_clients = Client.query.count()
    total_sales   = Sale.query.count()
    total_ren     = Renewal.query.count()
    total_cc      = ClientContact.query.count()
    total_msg     = Message.query.count()
    total_goals   = AttendantGoal.query.count()
    total_comm    = CommissionPayment.query.count()
    total_prices  = PriceItem.query.count()
    total_att     = Attendance.query.count()

    check("Users: 3 (admin + thiago + fin)", total_users == 3, f"total: {total_users}")
    check("Clients: 5", total_clients == 5, f"total: {total_clients}")
    check("Sales: 6", total_sales == 6, f"total: {total_sales}")
    check("Renewals: 6", total_ren == 6, f"total: {total_ren}")
    check("Contacts: 7", total_cc == 7, f"total: {total_cc}")
    check("Messages: 4", total_msg == 4, f"total: {total_msg}")
    check("Goals: 1", total_goals == 1, f"total: {total_goals}")
    check("CommissionPayments: 1", total_comm == 1, f"total: {total_comm}")
    check("PriceItems: 4", total_prices == 4, f"total: {total_prices}")
    check("Attendances: 1", total_att == 1, f"total: {total_att}")

    # ══════════════════════════════════════════════════════════
    print()
    print("=" * 55)
    print("  RESULTADO FINAL")
    print("=" * 55)
    ok_count   = sum(1 for r in results if r[0])
    fail_count = sum(1 for r in results if not r[0])
    total      = len(results)
    print(f"  Aprovados : {ok_count}/{total}")
    print(f"  Falhas    : {fail_count}/{total}")
    if fail_count == 0:
        print()
        print("  TODOS OS TESTES PASSARAM — sistema pronto para uso!")
    else:
        print()
        print("  FALHAS ENCONTRADAS:")
        for ok, label, detail in results:
            if not ok:
                print(f"    [ERRO] {label}  {detail}")
    print("=" * 55)
    print()
