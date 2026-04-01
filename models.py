from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from utils import now_br, today_br

db = SQLAlchemy()


class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='attendant')
    is_active = db.Column(db.Boolean, default=True)
    monthly_salary     = db.Column(db.Float,   nullable=True, default=0.0)  # salário mensal em R$
    work_hours_per_day = db.Column(db.Integer, nullable=True, default=8)   # carga horária diária esperada
    work_days_per_month = db.Column(db.Integer, nullable=True, default=26) # dias trabalhados por mês
    shift_end_hour      = db.Column(db.Integer, nullable=True, default=22)  # hora de término do turno (8–22 padrão, 14 para turno manhã)
    created_at = db.Column(db.DateTime, default=now_br)

    attendances = db.relationship('Attendance', backref='user', lazy=True, foreign_keys='Attendance.user_id')
    sales = db.relationship('Sale', backref='attendant', lazy=True, foreign_keys='Sale.attendant_id')
    clients = db.relationship('Client', backref='registered_by_user', lazy=True)
    overtime_requests = db.relationship('OvertimeRequest', backref='user', lazy=True, foreign_keys='OvertimeRequest.user_id')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_admin(self):
        return self.role == 'admin'

    def is_financial(self):
        return self.role == 'financial'

    def is_attendant(self):
        return self.role == 'attendant'

    def is_manager(self):
        return self.role == 'gerente'

    def can_access_admin(self):
        """Admin ou gerente podem acessar o painel de gestão."""
        return self.role in ('admin', 'gerente')

    @property
    def hourly_rate(self):
        """Valor por hora baseado no salário mensal (dias_mês × horas/dia)."""
        salary = self.monthly_salary or 0
        hours  = self.work_hours_per_day or 8
        days   = self.work_days_per_month or 22
        return salary / (days * hours) if salary > 0 else 0

    @property
    def daily_rate(self):
        """Valor por dia."""
        salary = self.monthly_salary or 0
        days   = self.work_days_per_month or 22
        return salary / days if salary > 0 else 0

    def monthly_salary_summary(self, year, month):
        """Retorna dict com resumo salarial do mês: esperado, trabalhado, déficit, desconto."""
        import calendar as _cal
        from datetime import date as _date
        first = _date(year, month, 1)
        last  = _date(year, month, _cal.monthrange(year, month)[1])

        hours_per_day = self.work_hours_per_day or 8
        rate = self.hourly_rate

        month_atts = [a for a in self.attendances
                      if first <= a.check_in.date() <= last]

        worked_minutes = sum(a.net_minutes for a in month_atts)
        deficit_mins   = sum(a.deficit_minutes(hours_per_day) for a in month_atts)
        days_worked    = len(month_atts)

        # Adiciona faltas injustificadas ao déficit
        absences = AbsenceRecord.query.filter_by(user_id=self.id).filter(
            AbsenceRecord.absence_date >= first,
            AbsenceRecord.absence_date <= last,
            AbsenceRecord.type == 'unjustified',
        ).all()
        absence_count = len(absences)
        deficit_mins += absence_count * hours_per_day * 60

        expected_minutes = (days_worked + absence_count) * hours_per_day * 60
        deduction = round(deficit_mins / 60 * rate, 2)

        return {
            'days_worked': days_worked,
            'absence_count': absence_count,
            'worked_minutes': worked_minutes,
            'worked_h': f"{worked_minutes // 60}h{worked_minutes % 60:02d}m",
            'expected_minutes': expected_minutes,
            'expected_h': f"{expected_minutes // 60}h{expected_minutes % 60:02d}m",
            'deficit_minutes': deficit_mins,
            'deficit_h': f"{deficit_mins // 60}h{deficit_mins % 60:02d}m",
            'deduction': deduction,
            'net_salary': round((self.monthly_salary or 0) - deduction, 2),
        }

    @property
    def active_attendance(self):
        return Attendance.query.filter_by(user_id=self.id, check_out=None).order_by(Attendance.check_in.desc()).first()

    @property
    def approved_overtime_today(self):
        today = today_br()
        return OvertimeRequest.query.filter(
            OvertimeRequest.user_id == self.id,
            OvertimeRequest.status == 'approved',
            db.func.date(OvertimeRequest.requested_at) == today
        ).first()

    @property
    def pending_overtime_today(self):
        today = today_br()
        return OvertimeRequest.query.filter(
            OvertimeRequest.user_id == self.id,
            OvertimeRequest.status == 'pending',
            db.func.date(OvertimeRequest.requested_at) == today
        ).first()


BREAK_ALLOWED_MINUTES = 20


class Attendance(db.Model):
    __tablename__ = 'attendances'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    check_in = db.Column(db.DateTime, nullable=False, default=now_br)
    check_out = db.Column(db.DateTime, nullable=True)
    date = db.Column(db.Date, nullable=False, default=today_br)

    breaks = db.relationship('AttendanceBreak', backref='attendance', lazy=True)

    @property
    def duration(self):
        end = self.check_out or now_br()
        delta = end - self.check_in
        hours, remainder = divmod(int(delta.total_seconds()), 3600)
        minutes = remainder // 60
        return f"{hours:02d}h{minutes:02d}m"

    @property
    def active_break(self):
        return next((b for b in self.breaks if b.status == 'active'), None)

    @property
    def net_minutes(self):
        """Minutos trabalhados líquidos (descontando pausas)."""
        end = self.check_out or now_br()
        total = int((end - self.check_in).total_seconds() / 60)
        return max(0, total - self.total_break_minutes)

    def deficit_minutes(self, expected_hours_per_day=8):
        """Minutos a menos em relação à carga horária esperada (0 se dentro do turno em aberto)."""
        if not self.check_out:
            return 0  # ainda trabalhando, não desconta ainda
        expected = expected_hours_per_day * 60
        return max(0, expected - self.net_minutes)

    @property
    def total_break_minutes(self):
        total = 0
        for b in self.breaks:
            if b.ended_at:
                total += int((b.ended_at - b.started_at).total_seconds() / 60)
            elif b.status == 'active':
                total += int((now_br() - b.started_at).total_seconds() / 60)
        return total

    @property
    def total_extra_minutes(self):
        extra = 0
        for b in self.breaks:
            if b.extra_minutes:
                extra += b.extra_minutes
            elif b.status == 'active':
                elapsed = int((now_br() - b.started_at).total_seconds() / 60)
                extra += max(0, elapsed - BREAK_ALLOWED_MINUTES)
        return extra


class AttendanceBreak(db.Model):
    __tablename__ = 'attendance_breaks'
    id = db.Column(db.Integer, primary_key=True)
    attendance_id = db.Column(db.Integer, db.ForeignKey('attendances.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    started_at = db.Column(db.DateTime, nullable=False, default=now_br)
    ended_at = db.Column(db.DateTime, nullable=True)
    extra_minutes = db.Column(db.Integer, nullable=True, default=0)
    status = db.Column(db.String(20), nullable=False, default='active')  # active, completed

    @property
    def duration_minutes(self):
        end = self.ended_at or now_br()
        return int((end - self.started_at).total_seconds() / 60)

    @property
    def duration_str(self):
        mins = self.duration_minutes
        h, m = divmod(mins, 60)
        return f"{h:02d}h{m:02d}m" if h else f"{m}min"

    @property
    def is_overdue(self):
        return self.status == 'active' and self.duration_minutes > BREAK_ALLOWED_MINUTES


class OvertimeRequest(db.Model):
    __tablename__ = 'overtime_requests'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    requested_at = db.Column(db.DateTime, nullable=False, default=now_br)
    status = db.Column(db.String(20), nullable=False, default='pending')
    approved_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)

    approver = db.relationship('User', foreign_keys=[approved_by])


DAYS_AT_RISK = 5  # dias sem contato para considerar cliente em risco


class Client(db.Model):
    __tablename__ = 'clients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(20), nullable=True)
    whatsapp = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(120), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    registered_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=now_br)

    sales = db.relationship('Sale', backref='client', lazy=True)
    contacts = db.relationship('ClientContact', backref='client', lazy=True,
                               order_by='ClientContact.contacted_at.desc()')

    @property
    def last_contact(self):
        return self.contacts[0] if self.contacts else None

    @property
    def days_without_contact(self):
        if self.last_contact:
            delta = now_br() - self.last_contact.contacted_at
            return delta.days
        # sem nenhum contato — usa data de cadastro
        delta = now_br() - self.created_at
        return delta.days

    @property
    def is_at_risk(self):
        return self.days_without_contact >= DAYS_AT_RISK

    @property
    def phone_display(self):
        return self.whatsapp or self.phone or '—'


CONTACT_TAGS = {
    'renovacao':  ('Renovação',   'bi-arrow-repeat',          'rgba(6,182,212,.2)',   '#67e8f9'),
    'cobranca':   ('Cobrança',    'bi-cash-coin',             'rgba(245,158,11,.2)',  '#fcd34d'),
    'duvida':     ('Dúvida',      'bi-question-circle-fill',  'rgba(124,58,237,.2)',  '#a78bfa'),
    'suporte':    ('Suporte',     'bi-tools',                 'rgba(16,185,129,.2)',  '#6ee7b7'),
    'followup':   ('Follow-up',   'bi-telephone-forward-fill','rgba(99,102,241,.2)',  '#a5b4fc'),
    'reclamacao': ('Reclamação',  'bi-emoji-frown-fill',      'rgba(239,68,68,.2)',   '#fca5a5'),
    'elogio':     ('Elogio',      'bi-star-fill',             'rgba(251,191,36,.2)',  '#fde68a'),
    'outro':      ('Outro',       'bi-chat-left-text',        'rgba(255,255,255,.1)', '#7b7b9e'),
}


class ClientContact(db.Model):
    __tablename__ = 'client_contacts'
    id           = db.Column(db.Integer, primary_key=True)
    client_id    = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=False)
    attendant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    contacted_at = db.Column(db.DateTime, nullable=False, default=now_br)
    direction    = db.Column(db.String(20), nullable=False, default='incoming')
    channel      = db.Column(db.String(20), nullable=False, default='whatsapp')
    tag          = db.Column(db.String(30), nullable=True)
    event_type   = db.Column(db.String(20), nullable=False, default='manual')  # manual | view
    notes        = db.Column(db.Text, nullable=True)

    attendant = db.relationship('User', foreign_keys=[attendant_id])

    @property
    def direction_label(self):
        return 'Cliente nos contactou' if self.direction == 'incoming' else 'Nós contactamos'

    @property
    def channel_label(self):
        return {'whatsapp': 'WhatsApp', 'phone': 'Telefone',
                'email': 'E-mail', 'other': 'Outro'}.get(self.channel, self.channel)

    @property
    def tag_info(self):
        return CONTACT_TAGS.get(self.tag) if self.tag else None


class Renewal(db.Model):
    __tablename__ = 'renewals'
    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=True)
    client_name_manual = db.Column(db.String(120), nullable=True)
    plan_name = db.Column(db.String(120), nullable=False)
    amount = db.Column(db.Float, nullable=False, default=0.0)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending, renewed, cancelled
    renewed_at = db.Column(db.DateTime, nullable=True)
    attendant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    comprovante_filename = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=now_br)

    client = db.relationship('Client', backref='renewals', foreign_keys=[client_id])
    attendant = db.relationship('User', foreign_keys=[attendant_id])

    @property
    def client_display(self):
        if self.client:
            return self.client.name
        return self.client_name_manual or 'Cliente não informado'

    @property
    def is_overdue(self):
        return self.status == 'pending' and self.due_date < today_br()

    @property
    def status_label(self):
        return {'pending': 'Pendente', 'renewed': 'Renovado', 'cancelled': 'Cancelado'}.get(self.status, self.status)


class Message(db.Model):
    __tablename__ = 'messages'
    id         = db.Column(db.Integer, primary_key=True)
    sender_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    # sempre identifica o atendente da conversa (mesmo quando admin envia)
    attendant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    content    = db.Column(db.Text, nullable=True, default='')
    created_at = db.Column(db.DateTime, default=now_br)
    read_at       = db.Column(db.DateTime, nullable=True)
    file_name     = db.Column(db.String(255), nullable=True)   # nome salvo em disco
    file_type     = db.Column(db.String(20),  nullable=True)   # 'image' | 'audio'
    original_name = db.Column(db.String(255), nullable=True)   # nome original

    sender   = db.relationship('User', foreign_keys=[sender_id])
    attendant_user = db.relationship('User', foreign_keys=[attendant_id])

    @property
    def is_from_admin(self):
        return self.sender.role == 'admin'


class AttendantGoal(db.Model):
    __tablename__ = 'attendant_goals'
    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    year           = db.Column(db.Integer, nullable=False)
    month          = db.Column(db.Integer, nullable=False)
    sales_goal     = db.Column(db.Float, nullable=False, default=0.0)
    renewals_goal  = db.Column(db.Integer, nullable=False, default=0)

    user = db.relationship('User', foreign_keys=[user_id])
    __table_args__ = (db.UniqueConstraint('user_id', 'year', 'month', name='uq_goal_user_month'),)


class AbsenceRecord(db.Model):
    """Falta registrada pelo admin (não aparece no ponto automaticamente)."""
    __tablename__ = 'absence_records'
    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    absence_date = db.Column(db.Date, nullable=False)
    type         = db.Column(db.String(20), nullable=False, default='unjustified')  # unjustified | justified | vacation
    notes        = db.Column(db.Text, nullable=True)
    created_by   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=now_br)

    user    = db.relationship('User', foreign_keys=[user_id])
    creator = db.relationship('User', foreign_keys=[created_by])

    __table_args__ = (db.UniqueConstraint('user_id', 'absence_date', name='uq_absence_user_date'),)

    @property
    def type_label(self):
        return {'unjustified': 'Falta Injustificada',
                'justified':   'Falta Justificada',
                'vacation':    'Folga/Férias'}.get(self.type, self.type)

    @property
    def deducts(self):
        """Apenas faltas injustificadas geram desconto."""
        return self.type == 'unjustified'


class SalaryPayment(db.Model):
    """Registro de pagamento de salário mensal."""
    __tablename__ = 'salary_payments'
    id           = db.Column(db.Integer, primary_key=True)
    attendant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    year         = db.Column(db.Integer, nullable=False)
    month        = db.Column(db.Integer, nullable=False)
    amount       = db.Column(db.Float, nullable=False)
    paid_at      = db.Column(db.DateTime, nullable=False, default=now_br)
    paid_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes        = db.Column(db.Text, nullable=True)

    attendant = db.relationship('User', foreign_keys=[attendant_id])
    payer     = db.relationship('User', foreign_keys=[paid_by])


class CommissionPayment(db.Model):
    __tablename__ = 'commission_payments'
    id           = db.Column(db.Integer, primary_key=True)
    attendant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    year         = db.Column(db.Integer, nullable=False)
    month        = db.Column(db.Integer, nullable=False)
    amount       = db.Column(db.Float, nullable=False)
    paid_at      = db.Column(db.DateTime, nullable=False, default=now_br)
    paid_by      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes        = db.Column(db.Text, nullable=True)

    attendant = db.relationship('User', foreign_keys=[attendant_id])
    payer     = db.relationship('User', foreign_keys=[paid_by])


class PriceItem(db.Model):
    __tablename__ = 'price_items'
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(120), nullable=False)
    price        = db.Column(db.Float, nullable=False)
    description  = db.Column(db.Text, nullable=True)
    screens      = db.Column(db.Integer, nullable=True, default=1)   # quantidade de telas
    period_label = db.Column(db.String(30), nullable=True)           # ex: "15 dias", "1 mês"
    is_active    = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=now_br)


PAYMENT_METHODS = {
    'pix': 'Pix',
    'credito': 'Cartão de Crédito',
    'debito': 'Cartão de Débito',
    'cakto': 'Cakto'
}


class Sale(db.Model):
    __tablename__ = 'sales'
    id = db.Column(db.Integer, primary_key=True)
    attendant_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey('clients.id'), nullable=True)
    client_name_manual = db.Column(db.String(120), nullable=True)
    amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)
    commission_rate = db.Column(db.Float, nullable=False)
    commission_amount = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text, nullable=True)
    comprovante_filename = db.Column(db.String(255), nullable=True)
    is_overtime = db.Column(db.Boolean, default=False)
    screens     = db.Column(db.Integer, nullable=True, default=1)    # telas vendidas
    adjustment  = db.Column(db.Float, nullable=True, default=0.0)    # desconto (neg) / acréscimo (pos)
    created_at = db.Column(db.DateTime, nullable=False, default=now_br)

    @property
    def payment_display(self):
        return PAYMENT_METHODS.get(self.payment_method, self.payment_method)

    @property
    def client_display(self):
        if self.client:
            return self.client.name
        return self.client_name_manual or 'Cliente não informado'
