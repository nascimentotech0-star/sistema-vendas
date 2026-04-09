import os
import uuid
import hashlib
import calendar as cal
from datetime import datetime, date, timedelta
from utils import now_br, today_br
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (db, User, Attendance, AttendanceBreak, OvertimeRequest, Client, Sale, Renewal,
                    PAYMENT_METHODS, PANEL_OPTIONS, SUPPORT_OPTIONS,
                    BREAK_ALLOWED_MINUTES, DAYS_AT_RISK,
                    CommissionPayment, PriceItem, AttendantGoal)
from flask import jsonify as _jsonify
from audit import log_action

attendant_bp = Blueprint('attendant', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}


def attendant_required(f):
    """Atendentes e gerentes podem acessar estas rotas."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not current_user.is_active:
            flash('Sua conta está desativada. Contate o administrador.', 'danger')
            return redirect(url_for('auth.logout'))
        # Apenas financeiro e admin puro são bloqueados aqui
        if current_user.is_financial():
            return redirect(url_for('financial.index'))
        if current_user.is_admin():
            # Admin puro não precisa de rotas de atendente, mas não bloqueia — apenas redireciona
            pass
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _extract_comprovante_dt(raw, ext):
    """Tenta extrair o datetime via EXIF (funciona para fotos de câmera; WhatsApp remove EXIF)."""
    if ext in ('jpg', 'jpeg', 'png', 'webp'):
        try:
            import io as _io
            from PIL import Image
            img = Image.open(_io.BytesIO(raw))
            exif = img._getexif() if hasattr(img, '_getexif') else None
            if exif:
                for tag_id in (36867, 36868, 306):
                    val = exif.get(tag_id)
                    if val:
                        try:
                            return datetime.strptime(str(val)[:19], '%Y:%m:%d %H:%M:%S')
                        except Exception:
                            continue
        except Exception:
            pass
    return None


def _extract_time_from_ocr(raw, ext):
    """Lê o horário visível no comprovante via OCR (Tesseract).

    Retorna (hora, minuto) se encontrar um horário no formato HH:MM dentro da imagem,
    ou None se não conseguir. Funciona com screenshots de comprovantes PIX, TED etc.
    """
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        return None
    try:
        import io as _io
        import re
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter

        img = Image.open(_io.BytesIO(raw)).convert('L')   # escala de cinza
        # Aumenta contraste para melhorar OCR em screenshots com fundo colorido
        img = ImageEnhance.Contrast(img).enhance(2.0)
        img = img.filter(ImageFilter.SHARPEN)

        text = pytesseract.image_to_string(img, lang='por+eng',
                                           config='--psm 6 -c tessedit_char_whitelist=0123456789:/APMapm ')

        # Remove padrões de data (DD/MM/AAAA ou DD/MM/AA) para não confundir com horário
        text = re.sub(r'\b\d{1,2}/\d{1,2}/\d{2,4}\b', '', text)

        # Busca padrões de horário: HH:MM ou H:MM (24h ou 12h)
        matches = re.findall(r'\b([01]?\d|2[0-3]):([0-5]\d)\b', text)
        if not matches:
            return None

        # Filtra horas impossíveis (ex: 99:99)
        valid = [(int(h), int(m)) for h, m in matches if int(h) <= 23 and int(m) <= 59]
        if not valid:
            return None

        # Usa a MENOR hora encontrada — comprovantes geralmente mostram a hora da
        # transação (menor) antes de mostrar a hora de emissão do recibo (maior)
        return min(valid, key=lambda x: x[0] * 60 + x[1])

    except Exception:
        return None


def _is_overtime_for_sale(comp_dt=None, form_time_str=None, ocr_time=None):
    """Determina se a venda é hora extra.

    Prioridade: formulário manual > OCR do comprovante > EXIF > hora do servidor.
    """
    shift_end = _shift_end()
    check_h   = None

    # 1. Hora informada manualmente no formulário
    if form_time_str:
        try:
            h, _ = map(int, form_time_str.strip().split(':'))
            check_h = h
        except Exception:
            pass

    # 2. OCR — hora lida diretamente do texto visível no comprovante
    if check_h is None and ocr_time is not None:
        check_h = ocr_time[0]

    # 3. EXIF — funciona apenas para fotos tiradas diretamente da câmera
    if check_h is None and comp_dt is not None:
        check_h = comp_dt.hour

    # 4. Fallback: hora atual do servidor
    if check_h is None:
        check_h = now_br().hour

    return not (8 <= check_h < shift_end)


def _process_comprovante(file_field='comprovante'):
    """Lê o comprovante do request, valida, salva e retorna (filename, sha256_hash, comp_dt).

    comp_dt: datetime extraído do EXIF/metadados do arquivo (ou None).
    Se nenhum arquivo for enviado devolve (None, None, None).
    Se o arquivo for duplicado levanta ValueError com mensagem descritiva.
    """
    file = request.files.get(file_field)
    if not file or not file.filename or not allowed_file(file.filename):
        return None, None, None

    raw    = file.read()
    sha256 = hashlib.sha256(raw).hexdigest()

    # Verificar duplicata
    dup = Sale.query.filter_by(comprovante_hash=sha256).first()
    if dup:
        when = dup.created_at.strftime('%d/%m/%Y às %H:%M')
        who  = dup.attendant.name.split()[0] if dup.attendant else 'outro atendente'
        raise ValueError(
            f'Comprovante duplicado! Este arquivo já foi enviado em {when} '
            f'por {who} (venda #{dup.id}). '
            f'Se for um pagamento diferente, use outro comprovante.'
        )

    ext      = file.filename.rsplit('.', 1)[1].lower()
    fname    = f"{uuid.uuid4().hex}.{ext}"
    path     = os.path.join(current_app.config['UPLOAD_FOLDER'], fname)
    comp_dt  = _extract_comprovante_dt(raw, ext)
    ocr_time = _extract_time_from_ocr(raw, ext)  # lê horário visível na imagem

    with open(path, 'wb') as f:
        f.write(raw)

    return fname, sha256, comp_dt, ocr_time


def _shift_end():
    """Retorna a hora de término do turno do usuário logado (padrão 22)."""
    try:
        return current_user.shift_end_hour or 22
    except Exception:
        return 22


COMMISSION_MIN = 5.0   # % base (zero vendas no mês)
COMMISSION_MAX = 10.0  # % máxima (ao bater a meta de vendas)

_MOTIVATIONAL = [
    # Foco e determinação
    "Cada cliente atendido é um passo mais perto da sua meta!",
    "Você está construindo algo grande hoje. Continue!",
    "O sucesso de hoje é a base do amanhã. Vamos nessa!",
    "Cada 'sim' que você recebe representa o seu esforço e dedicação.",
    "Acredite no seu potencial — você tem tudo para superar a meta!",
    "Seja o atendimento que você gostaria de receber. Excelência sempre!",
    "Quanto mais você se dedica, mais a sua comissão cresce. Bora vender!",
    "A sua persistência de hoje faz a diferença no seu bolso amanhã.",
    "Foco, energia e atitude — você tem os três. Use-os!",
    "Um atendimento de qualidade abre portas para muitas renovações.",
    "Cada contato pode ser o início de uma parceria longa. Capricha!",
    "Não é sorte — é preparação + esforço + momento. Você está pronto!",
    "A meta não espera. Mas você também não precisa esperar — comece agora!",
    "Clientes bem atendidos voltam e indicam outros. Faça a diferença!",
    "Sua comissão cresce com suas vendas. Cada R$ conta!",
    "Grandes resultados começam com pequenas atitudes consistentes.",
    "A diferença entre o comum e o extraordinário é um esforço a mais.",
    "Você não está aqui para passar o tempo — está aqui para fazer história.",
    "Cada ligação é uma oportunidade que só existe uma vez. Aproveite!",
    "Quem atende bem hoje constrói a carteira do futuro.",
    "A persistência é a chave que abre portas que o talento sozinho não abre.",
    "O cliente não compra produto — compra confiança. Seja digno dela.",
    "Não espere a motivação chegar. Aja e ela virá junto.",
    "Um dia produtivo começa com a decisão de torná-lo produtivo.",
    "Você está mais perto da sua meta do que estava ontem. Continue!",
    # Crescimento e comissão
    "Cada venda é um tijolo na construção do seu sucesso financeiro.",
    "Sua comissão de hoje paga o seu amanhã. Bora!",
    "Quanto mais você vende, mais sua porcentagem cresce. Estratégia!",
    "A meta de 10% de comissão está ao seu alcance — siga em frente!",
    "Pense no quanto você quer ganhar e trabalhe de acordo com isso.",
    "O esforço de hoje já está sendo contabilizado na sua comissão.",
    "Cada R$ vendido é um R$ a mais na sua conta. Foco!",
    "Você tem o poder de controlar quanto vai ganhar esse mês. Use-o!",
    "Não existe teto para quem atende com qualidade e dedicação.",
    "A comissão máxima está esperando por quem não desiste no meio do caminho.",
    "Pequenas vendas constantes constroem grandes comissões mensais.",
    "O sistema acompanha cada venda sua. Que tal surpreender hoje?",
    "Quanto mais você atende, mais o sistema trabalha a seu favor.",
    "Bater a meta não é o fim — é o começo de uma nova jornada.",
    "Você cresceu junto com o sistema. O sistema cresceu por causa de você.",
    # Atendimento e relacionamento
    "Um sorriso no atendimento vale mais do que qualquer script.",
    "O cliente sente quando você realmente quer ajudá-lo. Seja genuíno!",
    "Construa relacionamentos, não só transações.",
    "Um cliente satisfeito não precisa de desconto para voltar.",
    "Ouça mais, fale menos — e venda muito mais.",
    "A empatia é a ferramenta mais poderosa de um bom atendente.",
    "Resolva o problema do cliente antes de pensar na venda.",
    "Clientes não compram de empresas — compram de pessoas.",
    "O seu nome está por trás de cada venda. Honre-o.",
    "Atender bem é respeitar o tempo e a confiança do cliente.",
    "O pós-venda começa no momento da venda. Cuide desde o início.",
    "Um cliente que confia em você indica outros sem que você peça.",
    "Seja a razão pela qual o cliente escolhe a Nascimento Tech.",
    "A consistência no atendimento cria uma base sólida de clientes fiéis.",
    "Trate cada cliente como o mais importante — porque para ele, você é.",
    # Tecnologia e inovação
    "O sistema que você usa é tão poderoso quanto quem o opera. Seja o melhor!",
    "Tecnologia e dedicação juntos: essa é a fórmula do nosso sucesso.",
    "Cada recurso do sistema foi pensado para facilitar o seu trabalho.",
    "Use todas as ferramentas disponíveis — elas existem para você brilhar.",
    "Dados + atitude = resultado. Você tem os dois aqui.",
    "O sistema registra cada conquista sua. Que conquista vai ter hoje?",
    "Conectado, organizado e focado — assim é o atendente de alta performance.",
    "A tecnologia tira o trabalho braçal. Sobra mais tempo para você encantar.",
    "Cada venda registrada aqui é uma prova do seu trabalho. Orgulhe-se!",
    "O futuro do atendimento é digital. Você já está nele.",
    # Equipe e crescimento coletivo
    "Crescer junto é mais poderoso do que crescer sozinho.",
    "Quando você bate sua meta, inspira toda a equipe a bater a dela.",
    "Seu sucesso individual fortalece o time inteiro.",
    "Seja o atendente que os outros querem ser.",
    "A energia que você traz para o trabalho é contagiante. Use isso a seu favor.",
    "Equipes de alta performance são feitas de indivíduos comprometidos como você.",
    "O seu crescimento aqui é parte do crescimento de todos.",
    "Compartilhe o que funciona — equipes que aprendem juntas vencem juntas.",
    "Cada um de nós tem um papel. O seu é fundamental.",
    "O que você constrói hoje, a equipe colhe amanhã.",
    # Resiliência e superação
    "Um 'não' hoje pode ser o 'sim' de amanhã. Não desanime!",
    "Dias difíceis não duram, mas pessoas determinadas sim.",
    "O cansaço de hoje é o combustível do orgulho de amanhã.",
    "Tropeçar faz parte — levantar é o que define o campeão.",
    "Cada objeção do cliente é uma chance de mostrar seu valor.",
    "Não existe fracasso, apenas aprendizado que ainda não virou resultado.",
    "O mais difícil não é começar — é continuar quando fica difícil. Continue!",
    "Grandes profissionais são forjados nos dias mais desafiadores.",
    "O desconforto de hoje é a zona de conforto de amanhã.",
    "Quando a vontade supera o obstáculo, o obstáculo some.",
    "Resista à tentação de desistir — o resultado está mais perto do que parece.",
    "Cada desafio superado te deixa mais preparado para o próximo.",
    "A sua história de sucesso está sendo escrita agora, neste momento.",
    "Não compare seu começo com o meio da jornada de outro.",
    "Seja paciente com o processo e implacável com o esforço.",
    # Propósito e missão
    "Você não está só vendendo — está transformando a vida de cada cliente.",
    "Conectar pessoas a soluções é uma missão nobre. Seja orgulhoso disso.",
    "Por trás de cada venda há uma família que vai sorrir. Pense nisso.",
    "O trabalho com propósito nunca parece trabalho.",
    "Você é a ponte entre o problema do cliente e a solução que ele precisa.",
    "Cada cliente bem atendido é uma contribuição real para a sociedade.",
    "Faça seu trabalho de um jeito que valha a pena ser lembrado.",
    "A excelência não é um destino — é um hábito que você constrói dia a dia.",
    "Trabalhar com propósito é a forma mais eficiente de crescer.",
    "Quando você acredita no que vende, o cliente também acredita.",
    # Manhã / início de turno
    "Bom dia! O dia começa aqui — e pode ser incrível. Depende só de você.",
    "A primeira venda do dia é sempre a mais especial. Vai em busca dela!",
    "Comece o dia com intenção e termine com resultado.",
    "Hoje é mais uma chance de superar quem você foi ontem.",
    "Abriu o painel, já ganhou — agora é só converter o potencial em resultado.",
    "O dia ainda está em branco. Você escreve a história.",
    "Cada manhã é um reset. Use esse novo começo com inteligência.",
    "A melhor hora de plantar era ontem. A segunda melhor hora é agora.",
    "Acorde, organize, atenda, venda. Simples assim.",
    "Novos clientes estão esperando por alguém como você. Vai lá!",
    # Tarde / meio de turno
    "Já fez boas vendas? Ótimo — mas o dia ainda não acabou!",
    "Mantenha o ritmo. Os melhores resultados chegam para quem não para.",
    "Se ainda não bateu a meta, o turno ainda não acabou. Continue!",
    "A energia do meio do turno é o que separa os bons dos grandes.",
    "Não olhe para o relógio — olhe para a próxima oportunidade.",
    "Cada hora do seu turno tem valor. Não desperdice nenhuma.",
    "O melhor atendimento do dia pode ser o próximo. Esteja pronto.",
    "Você ainda tem tempo para virar o jogo. Use-o!",
    "Consistência no meio do turno é o que gera grandes resultados no fim do mês.",
    "Respire fundo, foque e vai. Você consegue!",
    # Fim de turno / motivação final
    "O esforço de hoje já está gerando frutos que você vai colher em breve.",
    "Encerre o turno com a certeza de que deu o seu melhor.",
    "Cada dia bem trabalhado é um depósito na conta do seu sucesso.",
    "Fechar o dia com resultado é a melhor sensação. Corra atrás!",
    "O que você fez hoje ficou registrado. Amanhã, supere!",
    # Frases curtas e diretas
    "Vá em frente. O sucesso não espera.",
    "Foco. Força. Resultado.",
    "Atenda. Encante. Fidelize.",
    "Um cliente de cada vez. Com atenção total.",
    "Hoje é o dia. Você é a pessoa. O momento é agora.",
    "Meta. Foco. Ação.",
    "Cada 'oi' pode virar uma venda. Atenda com excelência.",
    "Aqui não há limite — só o que você impõe a si mesmo.",
    "Você foi feito para isso. Vá fundo.",
    "A comissão não vem sem o esforço. O esforço não fica sem recompensa.",
    "Bora! A meta não vai bater sozinha.",
    "Foco no cliente. Resultado no bolso.",
    "Mais um atendimento. Mais um passo.",
    "Hoje é diferente porque você decidiu que seria.",
    "Não existe 'mais tarde' em vendas. Existe agora.",
    # Frases sobre aprendizado
    "Cada cliente ensina algo novo. Aprenda com todos.",
    "Os melhores vendedores são os melhores ouvintes.",
    "Estude o cliente antes de apresentar a solução.",
    "Quem aprende rápido, cresce rápido. Esteja sempre aberto.",
    "O mercado muda. Quem se adapta, prospera.",
    "Feedback do cliente é ouro. Use-o para melhorar.",
    "Observe os melhores e adote o que funciona. Sem orgulho.",
    "Perguntas certas abrem mais portas do que argumentos perfeitos.",
    "Conhecimento do produto + empatia = venda garantida.",
    "Quanto mais você aprende, mais confiante você atende.",
    # Inspiração financeira
    "Seus sonhos têm um preço — e suas vendas pagam por eles.",
    "Cada venda te aproxima de um objetivo pessoal. Lembre-se dele!",
    "Trabalhe hoje pelo estilo de vida que você quer ter amanhã.",
    "O dinheiro da comissão representa tempo, esforço e dedicação. Valorize!",
    "Pense na sua meta financeira e deixe ela te guiar durante o turno.",
    "Grandes comissões não caem do céu — são construídas venda a venda.",
    "O que você faz hoje define o extrato bancário do próximo mês.",
    "Invista no seu resultado — ele investe de volta em você.",
    "A independência financeira começa com atitudes consistentes no dia a dia.",
    "Você merece o sucesso que está construindo. Não pare agora.",
    # Frases motivacionais clássicas adaptadas
    "A única maneira de fazer um grande trabalho é amar o que você faz.",
    "O caminho para o sucesso está sempre em construção.",
    "Não desista. O começo é sempre o mais difícil.",
    "Acredite que você pode e já está na metade do caminho.",
    "O sucesso é a soma de pequenos esforços repetidos dia após dia.",
    "A excelência nunca é um acidente — é sempre resultado de intenção.",
    "Grandes conquistas exigem grande comprometimento.",
    "Você não precisa ser perfeito — precisa ser consistente.",
    "Cada passo conta, mesmo quando o destino parece longe.",
    "Inspire-se, foque-se, aja. Sempre nessa ordem.",
    # Sobre o negócio Nascimento Tech
    "Você representa uma empresa que acredita no seu potencial. Honre isso.",
    "A Nascimento Tech cresce quando cada atendente dá o seu melhor.",
    "O nome da empresa está em cada atendimento seu. Deixe-o brilhar.",
    "Clientes satisfeitos são o maior patrimônio que construímos juntos.",
    "Cada venda fortalece o time, a empresa e o seu futuro aqui.",
    "Somos uma equipe — e você é uma peça essencial dela.",
    "A reputação da empresa é construída atendimento a atendimento.",
    "O crescimento coletivo começa com a excelência individual de cada um.",
    "Você faz parte de algo que está crescendo. Cresça junto!",
    "Aqui, o seu esforço é reconhecido e recompensado. Vale a pena!",
    # Mindset vencedor
    "Campeões não nascem prontos — são construídos no dia a dia.",
    "Pense grande, aja agora, ajuste no caminho.",
    "O limite está na sua mente. Quebre-o todos os dias.",
    "Alta performance não é dom — é disciplina aplicada com consistência.",
    "Se você quer resultados diferentes, comece com atitudes diferentes.",
    "Não espere condições perfeitas. Aja nas condições que você tem.",
    "O sucesso ama quem se prepara e respeita quem persiste.",
    "Você é mais forte do que qualquer obstáculo que aparecer hoje.",
    "Cada dia é uma nova oportunidade de ser o melhor versão de si mesmo.",
    "Os resultados de amanhã são construídos pelas escolhas de hoje.",
    "A vitória tem gosto melhor quando você sabe o quanto trabalhou por ela.",
    "Quem planta com dedicação colhe com abundância.",
    "Seu próximo cliente pode ser o início de uma virada no seu mês.",
    "O esforço invisível de hoje será o resultado visível de amanhã.",
    "Cada 'não' que você supera te deixa mais preparado para o próximo 'sim'.",
    "Trabalhe enquanto outros dormem, atenda enquanto outros reclamam.",
    "A motivação te faz começar — o hábito te faz continuar.",
    "Você está exatamente onde precisa estar para crescer. Aproveite!",
    "Cada notificação de venda no sistema é prova do seu esforço valendo.",
    "Não meça o dia pelo cansaço — meça pelo quanto você entregou.",
    # Novas
    "Cada cliente é uma história. Faça a dele ter um final feliz.",
    "O seu melhor ainda está por vir — e começa neste exato momento.",
    "Não existe atalho para o topo, mas existe consistência. Use-a.",
    "Vendedores mediocres focam no produto. Grandes, focam no cliente.",
    "Transforme cada 'posso te ajudar?' em uma conexão real.",
    "Quem domina o atendimento, domina o resultado.",
    "Seja a diferença que o cliente não esperava — e vai lembrar para sempre.",
    "O entusiasmo é contagioso. Leve-o para cada conversa.",
    "Sua voz carrega a marca da empresa. Deixe-a soar com confiança.",
    "Cada renovação é prova de que você fez um bom trabalho antes.",
    "A segunda venda é mais fácil quando a primeira foi perfeita.",
    "Ganhar a confiança do cliente vale mais do que ganhar a discussão.",
    "Não venda um plano — venda tranquilidade, solução, futuro.",
    "O melhor fechamento é aquele que o cliente não percebe como pressão.",
    "Você é o principal ativo dessa operação. Invista em você.",
    "Sua atitude na segunda-feira define o ritmo da semana inteira.",
    "Metas existem para ser batidas — não admiradas de longe.",
    "Quando o cliente diz 'vou pensar', você ainda tem uma chance. Use-a!",
    "Cada script é um ponto de partida, não um roteiro fixo. Adapte-se.",
    "A persistência gentil vence a insistência agressiva sempre.",
    "O cliente mais difícil de conquistar é o mais fiel quando conquistado.",
    "Cuide bem de quem já é seu cliente — ele é seu melhor vendedor.",
    "Seja lembrado pela qualidade, não pelo preço.",
    "Você constrói a sua carteira de clientes tijolo a tijolo. Continue!",
    "Superar a meta não é sorte — é a soma de dias como este aqui.",
    "O cliente quer solução, não desculpa. Entregue sempre a solução.",
    "Quando o trabalho tem significado, o cansaço tem menos peso.",
    "Cada ponto percentual de comissão que sobe é fruto do seu esforço.",
    "Produtividade não é velocidade — é fazer o certo no tempo certo.",
    "O momento de se destacar é quando os outros estão reclamando.",
    "Cada conquista começa com a decisão de tentar mais uma vez.",
    "Você tem hoje o que pediu ontem. O que está pedindo para amanhã?",
    "Resultado é reflexo de escolhas. Escolha bem o que vai fazer agora.",
    "A próxima venda é a mais importante. Foque nela agora.",
    "Seja o atendente que o cliente conta para os amigos. Isso vale ouro.",
    "O sistema apoia. O produto é bom. O que falta é você ir lá. Vai!",
    "Fechar uma venda é a consequência de um ótimo atendimento.",
    "O cliente que hesita precisa de segurança. Transmita isso com confiança.",
    "Cada 'obrigado' do cliente é a confirmação de que você fez certo.",
    "Não existe dia fraco para quem tem propósito forte.",
    "Seu trabalho hoje tem impacto direto no seu bolso no fim do mês.",
    "Não espere inspiração para agir — aja e a inspiração aparece.",
    "Cada mês é uma nova temporada. Comece esta com tudo!",
    "Você tem em mãos as ferramentas — e o talento para usá-las bem.",
    "Seja o profissional que, ao fim do mês, não precisa de desculpas.",
    "Todo grande resultado começa com uma decisão simples: começar.",
    "A consistência bate o talento quando o talento não é consistente.",
    "Pense no que você quer realizar — depois aja como se já fosse possível.",
    "Quem celebra as pequenas vitórias tem energia para as grandes.",
    "Cada cliente bem atendido é um voto de confiança na Nascimento Tech.",
]


def progressive_rate(sales_count, target):
    """Comissão verdadeiramente progressiva: cada venda avança a taxa de forma suave.

    - sales_count : vendas já feitas este mês ANTES da venda atual
    - target      : meta mensal de vendas configurada para o atendente

    Taxa cresce linearmente: venda 0 → 5.00%, venda target → 10.00%.
    Exemplo com meta 700:
      venda #1   → 5.00%   (0 anteriores)
      venda #2   → 5.01%   (1 anterior  → 5 + 1/700*5 = 5.007 ≈ 5.01)
      venda #350 → 7.49%
      venda #700 → 9.99%
      venda #701+→ 10.00%
    """
    if target <= 0:
        return COMMISSION_MIN
    ratio = min(sales_count / float(target), 1.0)
    return round(COMMISSION_MIN + ratio * (COMMISSION_MAX - COMMISSION_MIN), 2)


def get_month_sales_count(user_id):
    """Número de vendas realizadas pelo atendente no mês corrente."""
    today = today_br()
    month_start = datetime(today.year, today.month, 1)
    month_end   = datetime(today.year, today.month,
                           cal.monthrange(today.year, today.month)[1]) + timedelta(days=1)
    return Sale.query.filter(
        Sale.attendant_id == user_id,
        Sale.created_at  >= month_start,
        Sale.created_at  <  month_end,
    ).count()


def get_commission_rate(sales_count=None):
    """Retorna a taxa de comissão para a PRÓXIMA venda a ser registrada.

    Fora do horário comercial → 20% (hora extra).
    Dentro do horário        → progressiva 5%–10% baseada em qtd de vendas no mês.

    Parâmetro sales_count opcional: passa o count já calculado para evitar re-consulta.
    """
    if not (8 <= now_br().hour < _shift_end()):
        return 20.0
    if sales_count is None:
        sales_count = get_month_sales_count(current_user.id)
    target = current_user.monthly_sales_target or 700
    return progressive_rate(sales_count, target)


def is_overtime_now():
    hour = now_br().hour
    return not (8 <= hour < _shift_end())


def can_request_overtime_now():
    """Solicitação permitida 1h antes do fim do turno."""
    hour = now_br().hour
    end  = _shift_end()
    return hour >= (end - 1)


# ── Dashboard ──────────────────────────────────────────────────────────────────

@attendant_bp.route('/')
@login_required
@attendant_required
def dashboard():
    today = today_br()
    day_start = datetime(today.year, today.month, today.day, 0, 0, 0)
    day_end   = day_start + timedelta(days=1)
    attendance = current_user.active_attendance

    today_sales = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        Sale.created_at >= day_start,
        Sale.created_at < day_end,
    ).order_by(Sale.created_at.desc()).all()

    today_total = sum(s.amount for s in today_sales)
    today_commission = sum(s.commission_amount for s in today_sales)

    # ── Renovações de hoje (deste atendente) ─────────────────────────────────
    today_renewals_list = Renewal.query.filter(
        Renewal.attendant_id == current_user.id,
        Renewal.created_at >= day_start,
        Renewal.created_at < day_end,
        Renewal.status == 'renewed',
    ).all()
    today_renewals_count = len(today_renewals_list)
    today_renewals_total = sum(r.amount for r in today_renewals_list if r.amount)

    overtime_req = OvertimeRequest.query.filter(
        OvertimeRequest.user_id == current_user.id,
        OvertimeRequest.requested_at >= day_start,
        OvertimeRequest.requested_at < day_end,
    ).first()

    overtime = is_overtime_now()
    commission_rate = 20.0 if overtime else None  # will be computed after month_total

    active_break = attendance.active_break if attendance else None
    can_request_overtime = can_request_overtime_now()

    # ── Gráficos (vendas do próprio atendente) ────────────────────────────────
    day_names = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

    w4_start = today - timedelta(days=27)
    sales_4w = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        db.func.date(Sale.created_at) >= w4_start
    ).all()
    day_totals = [0.0] * 7
    for s in sales_4w:
        day_totals[s.created_at.weekday()] += s.amount
    chart_weekday = {'labels': day_names, 'data': [round(v, 2) for v in day_totals]}

    w8_start = today - timedelta(weeks=8)
    sales_8w = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        db.func.date(Sale.created_at) >= w8_start
    ).all()
    week_map = {}
    for s in sales_8w:
        d = s.created_at.date()
        iso = d.isocalendar()
        key = f'{iso[0]}-S{iso[1]:02d}'
        week_map[key] = week_map.get(key, 0) + s.amount
    week_keys = sorted(week_map.keys())
    chart_weekly = {'labels': week_keys, 'data': [round(week_map[k], 2) for k in week_keys]}

    m12_start = today.replace(day=1) - timedelta(days=365)
    sales_12m = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        db.func.date(Sale.created_at) >= m12_start
    ).all()
    month_labels_pt = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    month_map = {}
    for s in sales_12m:
        key = s.created_at.strftime('%Y-%m')
        month_map[key] = month_map.get(key, 0) + s.amount
    month_keys = sorted(month_map.keys())
    chart_monthly = {
        'labels': [f"{month_labels_pt[int(k.split('-')[1])-1]}/{k.split('-')[0][2:]}" for k in month_keys],
        'data': [round(month_map[k], 2) for k in month_keys]
    }

    # ── Renovações do mês (clientes do atendente) ─────────────────────────────
    my_clients_ids = [c.id for c in Client.query.filter_by(registered_by=current_user.id).all()]
    first_month = date(today.year, today.month, 1)
    last_month  = date(today.year, today.month, cal.monthrange(today.year, today.month)[1])
    my_renewals = Renewal.query.filter(
        Renewal.client_id.in_(my_clients_ids),
        Renewal.due_date >= first_month,
        Renewal.due_date <= last_month
    ).order_by(Renewal.due_date).all() if my_clients_ids else []

    renewals_pending  = [r for r in my_renewals if r.status == 'pending']
    renewals_overdue  = [r for r in my_renewals if r.is_overdue]
    renewals_done     = sum(1 for r in my_renewals if r.status == 'renewed')

    # ── Clientes em risco (sem contato há 5+ dias) — todos os clientes ──────────
    all_clients = Client.query.all()
    at_risk_clients = sorted(
        [c for c in all_clients if c.is_at_risk],
        key=lambda c: c.days_without_contact, reverse=True
    )

    # ── Comissão acumulada no mês ─────────────────────────────────────────────
    month_start = datetime(today.year, today.month, 1)
    month_end   = datetime(today.year, today.month,
                           cal.monthrange(today.year, today.month)[1]) + timedelta(days=1)
    month_sales = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        Sale.created_at >= month_start,
        Sale.created_at < month_end,
    ).all()
    month_total      = sum(s.amount for s in month_sales)
    month_commission = sum(s.commission_amount for s in month_sales)

    # Comissão progressiva: baseada em número de vendas (não em R$)
    sales_target        = current_user.monthly_sales_target or 700
    month_sales_count   = len(month_sales)   # vendas já feitas no mês
    current_rate        = get_commission_rate(month_sales_count)
    commission_progress = min(int(month_sales_count / sales_target * 100), 100)
    sales_remaining     = max(sales_target - month_sales_count, 0)

    # ── Metas do mês (AttendantGoal) ─────────────────────────────────────────
    goal = AttendantGoal.query.filter_by(
        user_id=current_user.id,
        year=today.year, month=today.month
    ).first()
    goal_sales_target   = goal.sales_goal    if goal and goal.sales_goal    else 0.0
    goal_renewal_target = goal.renewals_goal if goal and goal.renewals_goal else 0
    goal_sales_pct      = min(int(month_total / goal_sales_target * 100), 100) if goal_sales_target > 0 else 0
    goal_renewal_pct    = min(int(renewals_done / goal_renewal_target * 100), 100) if goal_renewal_target > 0 else 0

    # ── Renovações do mês (deste atendente) ──────────────────────────────────
    month_renewals_list = Renewal.query.filter(
        Renewal.attendant_id == current_user.id,
        Renewal.created_at >= month_start,
        Renewal.created_at < month_end,
        Renewal.status == 'renewed',
    ).all()
    month_renewals_count = len(month_renewals_list)
    month_renewals_total = sum(r.amount for r in month_renewals_list if r.amount)

    # ── Ranking de VENDAS do mês (todos os atendentes) ────────────────────────
    ranking_rows = (
        db.session.query(
            User.id,
            User.name,
            db.func.sum(Sale.amount).label('total'),
            db.func.count(Sale.id).label('count'),
        )
        .join(Sale, Sale.attendant_id == User.id)
        .filter(Sale.created_at >= month_start, Sale.created_at < month_end)
        .group_by(User.id, User.name)
        .order_by(db.func.sum(Sale.amount).desc())
        .all()
    )
    ranking_all = [
        {'id': r.id, 'name': r.name.split()[0], 'total': float(r.total), 'count': r.count}
        for r in ranking_rows
    ]
    my_rank_pos = next((i + 1 for i, r in enumerate(ranking_all) if r['id'] == current_user.id), None)
    ranking_top = ranking_all[:5]

    # ── Ranking de RENOVAÇÕES do mês (todos os atendentes) ───────────────────
    renewal_ranking_rows = (
        db.session.query(
            User.id,
            User.name,
            db.func.count(Renewal.id).label('count'),
            db.func.sum(Renewal.amount).label('total'),
        )
        .join(Renewal, Renewal.attendant_id == User.id)
        .filter(
            Renewal.created_at >= month_start,
            Renewal.created_at < month_end,
            Renewal.status == 'renewed',
        )
        .group_by(User.id, User.name)
        .order_by(db.func.count(Renewal.id).desc())
        .all()
    )
    renewal_ranking_all = [
        {'id': r.id, 'name': r.name.split()[0], 'count': r.count, 'total': float(r.total or 0)}
        for r in renewal_ranking_rows
    ]
    my_renewal_rank_pos = next((i + 1 for i, r in enumerate(renewal_ranking_all) if r['id'] == current_user.id), None)
    renewal_ranking_top = renewal_ranking_all[:5]

    # ── Fila de prioridades do dia ─────────────────────────────────────────────
    in_3_days = today + timedelta(days=3)
    priorities = []

    # 1. Renovações vencendo hoje (máxima urgência)
    for r in renewals_pending:
        if r.due_date == today:
            priorities.append({
                'urgency': 'danger',
                'icon': 'bi-exclamation-circle-fill',
                'label': 'Renova HOJE',
                'client': r.client.name,
                'client_id': r.client_id,
            })

    # 2. Renovações já vencidas
    for r in renewals_overdue:
        days_late = (today - r.due_date).days
        priorities.append({
            'urgency': 'danger',
            'icon': 'bi-x-circle-fill',
            'label': f'Vencida há {days_late} dia{"s" if days_late != 1 else ""}',
            'client': r.client.name,
            'client_id': r.client_id,
        })

    # 3. Clientes sem contato (próprios do atendente)
    my_at_risk = [c for c in at_risk_clients if c.registered_by == current_user.id]
    for c in my_at_risk[:8]:
        priorities.append({
            'urgency': 'warning',
            'icon': 'bi-person-exclamation',
            'label': f'{c.days_without_contact} dia{"s" if c.days_without_contact != 1 else ""} sem contato',
            'client': c.name,
            'client_id': c.id,
        })

    # 4. Renovações vencendo nos próximos 3 dias (preventivo)
    for r in renewals_pending:
        if today < r.due_date <= in_3_days:
            days_until = (r.due_date - today).days
            priorities.append({
                'urgency': 'info',
                'icon': 'bi-clock-fill',
                'label': f'Renova em {days_until} dia{"s" if days_until != 1 else ""}',
                'client': r.client.name,
                'client_id': r.client_id,
            })

    # ── Streak: dias consecutivos com pelo menos 1 venda ─────────────────────
    streak = 0
    check_day = today - timedelta(days=1)  # começa no dia anterior
    # conta hoje separado (só se tiver venda)
    if today_sales:
        streak = 1
        while True:
            ds = datetime(check_day.year, check_day.month, check_day.day)
            de = ds + timedelta(days=1)
            count = Sale.query.filter(
                Sale.attendant_id == current_user.id,
                Sale.created_at >= ds,
                Sale.created_at < de,
            ).count()
            if count > 0:
                streak += 1
                check_day -= timedelta(days=1)
            else:
                break

    # ── Recorde pessoal do dia (maior total em 1 dia) ─────────────────────────
    best_day_result = (
        db.session.query(
            db.func.date(Sale.created_at).label('day'),
            db.func.sum(Sale.amount).label('total')
        )
        .filter(Sale.attendant_id == current_user.id)
        .group_by(db.func.date(Sale.created_at))
        .order_by(db.func.sum(Sale.amount).desc())
        .first()
    )
    personal_best = float(best_day_result.total) if best_day_result else 0.0
    personal_best_date = best_day_result.day if best_day_result else None

    # ── Feed da equipe: últimas 8 vendas de todos atendentes ─────────────────
    team_feed = (
        Sale.query
        .filter(Sale.created_at >= datetime(today.year, today.month, today.day) - timedelta(days=1))
        .order_by(Sale.created_at.desc())
        .limit(8)
        .all()
    )

    # ── Mensagem motivacional aleatória ──────────────────────────────────────
    import random
    motivational_msg = random.choice(_MOTIVATIONAL)

    # ── Resumo salarial do mês ────────────────────────────────────────────────
    salary_summary = current_user.monthly_salary_summary(today.year, today.month)

    # Déficit de hoje (ponto em aberto: projeção se encerrar agora)
    today_deficit_mins = 0
    today_net_mins = 0
    if attendance and attendance.check_out is None:
        today_net_mins = attendance.net_minutes
        expected = (current_user.work_hours_per_day or 8) * 60
        today_deficit_mins = max(0, expected - today_net_mins)

    return render_template('attendant/dashboard.html',
        attendance=attendance,
        today_sales=today_sales,
        today_total=today_total,
        today_commission=today_commission,
        today_renewals_count=today_renewals_count,
        today_renewals_total=today_renewals_total,
        overtime_req=overtime_req,
        commission_rate=commission_rate,
        is_overtime=overtime,
        payment_methods=PAYMENT_METHODS,
        now=now_br(),
        active_break=active_break,
        break_allowed=BREAK_ALLOWED_MINUTES,
        can_request_overtime=can_request_overtime,
        chart_weekday=chart_weekday,
        chart_weekly=chart_weekly,
        chart_monthly=chart_monthly,
        my_renewals=my_renewals,
        renewals_pending=renewals_pending,
        renewals_overdue=renewals_overdue,
        renewals_done=renewals_done,
        at_risk_clients=at_risk_clients,
        salary_summary=salary_summary,
        today_deficit_mins=today_deficit_mins,
        today_net_mins=today_net_mins,
        month_total=month_total,
        month_commission=month_commission,
        current_rate=current_rate,
        commission_progress=commission_progress,
        month_sales_count=month_sales_count,
        sales_target=sales_target,
        sales_remaining=sales_remaining,
        motivational_msg=motivational_msg,
        ranking=ranking_top,
        renewal_ranking=renewal_ranking_top,
        my_renewal_rank_pos=my_renewal_rank_pos,
        month_renewals_count=month_renewals_count,
        month_renewals_total=month_renewals_total,
        goal_sales_target=goal_sales_target,
        goal_renewal_target=goal_renewal_target,
        goal_sales_pct=goal_sales_pct,
        goal_renewal_pct=goal_renewal_pct,
        my_rank_pos=my_rank_pos,
        priorities=priorities,
        streak=streak,
        personal_best=personal_best,
        personal_best_date=personal_best_date,
        team_feed=team_feed,
    )


# ── Ponto ──────────────────────────────────────────────────────────────────────

@attendant_bp.route('/ponto/entrada', methods=['POST'])
@login_required
@attendant_required
def checkin():
    if current_user.active_attendance:
        flash('Você já iniciou o atendimento.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    att = Attendance(user_id=current_user.id, check_in=now_br(), date=today_br())
    db.session.add(att)
    db.session.commit()
    flash('Atendimento iniciado! Boas vendas!', 'success')
    return redirect(url_for('attendant.dashboard'))


@attendant_bp.route('/ponto/saida', methods=['POST'])
@login_required
@attendant_required
def checkout():
    att = current_user.active_attendance
    if not att:
        flash('Nenhum atendimento ativo.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    att.check_out = now_br()
    db.session.commit()
    flash(f'Atendimento encerrado. Duração: {att.duration}', 'success')
    return redirect(url_for('attendant.dashboard'))


# ── Renovações do atendente ────────────────────────────────────────────────────

@attendant_bp.route('/renovacoes')
@login_required
@attendant_required
def renewals():
    today = today_br()
    month = request.args.get('month', today.strftime('%Y-%m'))
    status_filter = request.args.get('status', '')

    try:
        year, mon = int(month.split('-')[0]), int(month.split('-')[1])
    except Exception:
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])

    my_client_ids = [c.id for c in Client.query.filter_by(registered_by=current_user.id).all()]

    # Mostra renovações dos clientes do atendente OU renovações que ele atendeu
    query = Renewal.query.filter(
        Renewal.due_date >= first_day,
        Renewal.due_date <= last_day,
        db.or_(
            Renewal.client_id.in_(my_client_ids) if my_client_ids else db.false(),
            Renewal.attendant_id == current_user.id
        )
    )

    if status_filter:
        query = query.filter_by(status=status_filter)

    all_renewals = query.order_by(Renewal.due_date).all()

    total     = len(all_renewals)
    renewed   = sum(1 for r in all_renewals if r.status == 'renewed')
    pending   = sum(1 for r in all_renewals if r.status == 'pending')
    cancelled = sum(1 for r in all_renewals if r.status == 'cancelled')
    overdue   = sum(1 for r in all_renewals if r.is_overdue)
    rate      = round((renewed / total * 100) if total > 0 else 0, 1)

    # ── Gráficos ──────────────────────────────────────────────────────────────
    day_names = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    w4_start = today - timedelta(days=27)
    all_4w = Renewal.query.filter(
        Renewal.client_id.in_(my_client_ids),
        Renewal.due_date >= w4_start
    ).all() if my_client_ids else []
    day_renewed   = [0]*7
    day_cancelled = [0]*7
    day_pending   = [0]*7
    for r in all_4w:
        dow = r.due_date.weekday()
        if r.status == 'renewed':    day_renewed[dow]   += 1
        elif r.status == 'cancelled': day_cancelled[dow] += 1
        else:                         day_pending[dow]   += 1
    chart_weekday = {'labels': day_names, 'renewed': day_renewed,
                     'cancelled': day_cancelled, 'pending': day_pending}

    m6_start = today.replace(day=1) - timedelta(days=180)
    all_6m = Renewal.query.filter(
        Renewal.client_id.in_(my_client_ids),
        Renewal.due_date >= m6_start
    ).all() if my_client_ids else []
    month_labels_pt = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    month_renewed  = {}
    month_cancelled = {}
    month_pending   = {}
    for r in all_6m:
        key = r.due_date.strftime('%Y-%m')
        if r.status == 'renewed':    month_renewed[key]   = month_renewed.get(key, 0) + 1
        elif r.status == 'cancelled': month_cancelled[key] = month_cancelled.get(key, 0) + 1
        else:                         month_pending[key]   = month_pending.get(key, 0) + 1
    month_keys = sorted(set(list(month_renewed) + list(month_cancelled) + list(month_pending)))
    chart_monthly = {
        'labels':    [f"{month_labels_pt[int(k.split('-')[1])-1]}/{k.split('-')[0][2:]}" for k in month_keys],
        'renewed':   [month_renewed.get(k, 0) for k in month_keys],
        'cancelled': [month_cancelled.get(k, 0) for k in month_keys],
        'pending':   [month_pending.get(k, 0) for k in month_keys],
    }

    my_clients = Client.query.order_by(Client.name).all()
    price_items = PriceItem.query.filter_by(is_active=True).order_by(PriceItem.price).all()

    return render_template('attendant/renewals.html',
        renewals=all_renewals,
        month=month,
        status_filter=status_filter,
        stats=dict(total=total, renewed=renewed, pending=pending,
                   cancelled=cancelled, overdue=overdue, rate=rate),
        chart_weekday=chart_weekday,
        chart_monthly=chart_monthly,
        my_clients=my_clients,
        price_items=price_items,
    )


# ── Ações de renovação (atendente) ─────────────────────────────────────────────

@attendant_bp.route('/renovacoes/<int:id>/renovar', methods=['POST'])
@login_required
@attendant_required
def att_renew(id):
    renewal = Renewal.query.get_or_404(id)

    # Comprovante obrigatório + anti-duplicata
    file = request.files.get('comprovante')
    if not file or not file.filename or not allowed_file(file.filename):
        flash('Comprovante de pagamento é obrigatório para confirmar a renovação.', 'danger')
        return redirect(url_for('attendant.renewals'))

    raw  = file.read()
    sha  = hashlib.sha256(raw).hexdigest()
    dup  = Sale.query.filter_by(comprovante_hash=sha).first()
    if dup:
        when = dup.created_at.strftime('%d/%m/%Y às %H:%M')
        who  = dup.attendant.name.split()[0] if dup.attendant else 'outro atendente'
        flash(f'Comprovante duplicado! Este arquivo já foi usado na venda #{dup.id} ({when} por {who}).', 'danger')
        return redirect(url_for('attendant.renewals'))

    ext = file.filename.rsplit('.', 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(current_app.config['UPLOAD_FOLDER'], fname), 'wb') as fh:
        fh.write(raw)

    # Atualiza valor se informado
    amount_str = request.form.get('amount', '').strip().replace(',', '.')
    if amount_str:
        try:
            renewal.amount = float(amount_str)
        except ValueError:
            pass

    renewal.status = 'renewed'
    renewal.renewed_at = now_br()
    renewal.attendant_id = current_user.id
    renewal.comprovante_filename = fname
    db.session.commit()
    flash(f'Renovação de "{renewal.client_display}" confirmada com comprovante!', 'success')
    return redirect(url_for('attendant.renewals'))


@attendant_bp.route('/renovacoes/<int:id>/cancelar', methods=['POST'])
@login_required
@attendant_required
def att_cancel(id):
    renewal = Renewal.query.get_or_404(id)
    renewal.status = 'cancelled'
    renewal.attendant_id = current_user.id
    db.session.commit()
    flash(f'Renovação de "{renewal.client_display}" marcada como cancelada.', 'warning')
    return redirect(url_for('attendant.renewals'))


@attendant_bp.route('/renovacoes/nova', methods=['POST'])
@login_required
@attendant_required
def att_new_renewal():
    client_id   = request.form.get('client_id') or None
    plan_name   = request.form.get('plan_name', '').strip()
    amount_str  = request.form.get('amount', '0').replace(',', '.')
    due_date_str = request.form.get('due_date', '')
    notes       = request.form.get('notes', '').strip() or None

    if not client_id or not plan_name or not due_date_str:
        flash('Cliente, plano e data de vencimento são obrigatórios.', 'danger')
        return redirect(url_for('attendant.renewals'))

    if not Client.query.get(int(client_id)):
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('attendant.renewals'))

    try:
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        amount   = float(amount_str)
    except Exception:
        flash('Data ou valor inválido.', 'danger')
        return redirect(url_for('attendant.renewals'))

    # Comprovante opcional ao cadastrar (para migração de clientes existentes)
    comprovante_filename = None
    file = request.files.get('comprovante')
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], fname))
        comprovante_filename = fname

    renewal = Renewal(
        client_id=int(client_id),
        plan_name=plan_name,
        amount=amount,
        due_date=due_date,
        attendant_id=current_user.id,
        notes=notes,
        comprovante_filename=comprovante_filename,
        status='pending',
    )
    db.session.add(renewal)
    db.session.commit()
    client_name = Client.query.get(int(client_id)).name
    flash(f'Renovação de {client_name} — {plan_name} cadastrada!', 'success')
    return redirect(url_for('attendant.renewals'))


# ── Pausa / Descanso ──────────────────────────────────────────────────────────

@attendant_bp.route('/pausa/iniciar', methods=['POST'])
@login_required
@attendant_required
def start_break():
    att = current_user.active_attendance
    if not att:
        flash('Inicie o atendimento antes de pausar.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    if att.active_break:
        flash('Você já está em pausa.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    brk = AttendanceBreak(
        attendance_id=att.id,
        user_id=current_user.id,
        started_at=now_br(),
        status='active'
    )
    db.session.add(brk)
    db.session.commit()
    flash(f'Pausa iniciada. Você tem {BREAK_ALLOWED_MINUTES} minutos de descanso.', 'info')
    return redirect(url_for('attendant.dashboard'))


@attendant_bp.route('/pausa/encerrar', methods=['POST'])
@login_required
@attendant_required
def end_break():
    att = current_user.active_attendance
    if not att or not att.active_break:
        flash('Nenhuma pausa ativa.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    brk = att.active_break
    brk.ended_at = now_br()
    brk.status = 'completed'
    duration = int((brk.ended_at - brk.started_at).total_seconds() / 60)
    brk.extra_minutes = max(0, duration - BREAK_ALLOWED_MINUTES)
    db.session.commit()
    if brk.extra_minutes > 0:
        flash(f'Pausa encerrada. Duração: {brk.duration_str}. '
              f'Você excedeu {brk.extra_minutes} minuto(s) — adicionado ao banco de horas.', 'warning')
    else:
        flash(f'Pausa encerrada. Duração: {brk.duration_str}. Bem-vindo de volta!', 'success')
    return redirect(url_for('attendant.dashboard'))


# ── Hora Extra ─────────────────────────────────────────────────────────────────

@attendant_bp.route('/hora-extra/solicitar', methods=['POST'])
@login_required
@attendant_required
def request_overtime():
    if not can_request_overtime_now():
        end = _shift_end()
        flash(f'Solicitação de hora extra só pode ser enviada a partir das {end - 1}h.', 'danger')
        return redirect(url_for('attendant.dashboard'))
    today = today_br()
    day_start = datetime(today.year, today.month, today.day)
    day_end   = day_start + timedelta(days=1)
    existing = OvertimeRequest.query.filter(
        OvertimeRequest.user_id == current_user.id,
        OvertimeRequest.requested_at >= day_start,
        OvertimeRequest.requested_at < day_end,
    ).first()
    if existing:
        flash('Você já enviou uma solicitação hoje.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    req = OvertimeRequest(user_id=current_user.id, requested_at=now_br(), status='pending')
    db.session.add(req)
    db.session.flush()
    from notify import notify_admins
    notify_admins(
        f'Hora extra solicitada — {current_user.name}',
        'Clique para aprovar ou negar a solicitação.',
        link='/admin/hora-extra',
        icon='bi-clock-history', color='#fcd34d'
    )
    db.session.commit()
    flash('Solicitação de hora extra enviada! Aguarde aprovação do administrador.', 'info')
    return redirect(url_for('attendant.dashboard'))


# ── Clientes ───────────────────────────────────────────────────────────────────

@attendant_bp.route('/clientes')
@login_required
@attendant_required
def clients():
    search       = request.args.get('q', '').strip()
    filter_panel = request.args.get('panel', '').strip()
    filter_sup   = request.args.get('support', '').strip()
    query = Client.query
    if search:
        query = query.filter(
            db.or_(
                Client.name.ilike(f'%{search}%'),
                Client.phone.ilike(f'%{search}%'),
                Client.whatsapp.ilike(f'%{search}%'),
            )
        )
    if filter_panel:
        query = query.filter(Client.panel_name == filter_panel)
    if filter_sup:
        query = query.filter(Client.support_type == filter_sup)
    clients_list = query.order_by(Client.name).all()
    return render_template('attendant/clients.html',
                           clients=clients_list, search=search,
                           panel_options=PANEL_OPTIONS, support_options=SUPPORT_OPTIONS,
                           filter_panel=filter_panel, filter_support=filter_sup)


@attendant_bp.route('/clientes/migrar', methods=['GET', 'POST'])
@login_required
@attendant_required
def migrate_client():
    """Migra cliente de outra plataforma — sem gerar venda/comissão.
    Cadastra o cliente e cria uma renovação com comprovante, sem comissão.
    """
    price_items     = PriceItem.query.filter_by(is_active=True).order_by(PriceItem.price).all()
    def _render_migrate(**kw):
        return render_template('attendant/migrate_client.html',
                               price_items=price_items,
                               panel_options=PANEL_OPTIONS,
                               support_options=SUPPORT_OPTIONS, **kw)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Nome é obrigatório.', 'danger')
            return _render_migrate()

        def _norm_phone(raw):
            digits = ''.join(c for c in (raw or '') if c.isdigit())
            if len(digits) == 13 and digits.startswith('55'):
                digits = digits[2:]
            return digits or None

        phone_raw    = request.form.get('phone', '').strip()
        whatsapp_raw = request.form.get('whatsapp', '').strip()
        phone_norm    = _norm_phone(phone_raw)
        whatsapp_norm = _norm_phone(whatsapp_raw)

        # Verifica duplicata
        dup_client = None
        name_lower = name.lower()
        for c in Client.query.all():
            if c.name.lower() == name_lower:
                dup_client = c; break
        if not dup_client and (phone_norm or whatsapp_norm):
            for c in Client.query.all():
                c_phone = _norm_phone(c.phone)
                c_wa    = _norm_phone(c.whatsapp)
                if phone_norm and phone_norm in (c_phone, c_wa):
                    dup_client = c; break
                if whatsapp_norm and whatsapp_norm in (c_phone, c_wa):
                    dup_client = c; break

        if dup_client:
            flash(
                f'Cliente "{dup_client.name}" já está cadastrado (ID #{dup_client.id}). '
                f'Se precisar adicionar uma renovação, use a tela de Renovações.',
                'warning'
            )
            return _render_migrate()

        # Cria o cliente
        client = Client(
            name=name,
            phone=phone_raw or None,
            whatsapp=whatsapp_raw or None,
            email=request.form.get('email', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            panel_name=request.form.get('panel_name', '').strip() or None,
            support_type=request.form.get('support_type', '').strip() or None,
            registered_by=current_user.id,
        )
        db.session.add(client)
        db.session.flush()

        # Comprovante de renovação (obrigatório)
        file = request.files.get('comprovante')
        if not file or not file.filename or not allowed_file(file.filename):
            flash('Comprovante do plano atual é obrigatório para migração.', 'danger')
            db.session.rollback()
            return _render_migrate()

        raw = file.read()
        sha = hashlib.sha256(raw).hexdigest()
        dup_sale = Sale.query.filter_by(comprovante_hash=sha).first()
        if dup_sale:
            flash(f'Comprovante duplicado — já usado na venda #{dup_sale.id}.', 'danger')
            db.session.rollback()
            return _render_migrate()

        ext   = file.filename.rsplit('.', 1)[-1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        with open(os.path.join(current_app.config['UPLOAD_FOLDER'], fname), 'wb') as fh:
            fh.write(raw)

        # Data de vencimento
        due_date_str = request.form.get('due_date', '')
        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except Exception:
            flash('Data de vencimento inválida.', 'danger')
            db.session.rollback()
            return _render_migrate()

        plan_name = request.form.get('plan_name', '').strip()
        try:
            amount = float(request.form.get('amount', '0').replace(',', '.'))
        except Exception:
            amount = 0.0

        renewal = Renewal(
            client_id=client.id,
            plan_name=plan_name,
            amount=amount,
            due_date=due_date,
            status='renewed',
            renewed_at=now_br(),
            attendant_id=current_user.id,
            comprovante_filename=fname,
            notes='Migrado de outra plataforma',
        )
        db.session.add(renewal)
        log_action('client_migrate', f'Cliente migrado: {name}', 'Client', client.id)
        db.session.commit()
        flash(f'Cliente "{name}" migrado com sucesso! Renovação registrada até {due_date.strftime("%d/%m/%Y")}.', 'success')
        return redirect(url_for('attendant.renewals'))

    return _render_migrate()


@attendant_bp.route('/clientes/novo', methods=['GET', 'POST'])
@login_required
@attendant_required
def new_client():
    overtime        = is_overtime_now()
    panel_options   = PANEL_OPTIONS
    support_options = SUPPORT_OPTIONS

    def _chart_data():
        today = today_br()
        day_start = datetime(today.year, today.month, today.day)
        day_end   = day_start + timedelta(days=1)
        week_start_dt = datetime(today.year, today.month, today.day) - timedelta(days=6)
        sales_7d = Sale.query.filter(
            Sale.attendant_id == current_user.id,
            Sale.created_at >= week_start_dt,
        ).all()
        day_labels, day_vals = [], []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            day_labels.append(d.strftime('%d/%m'))
            day_vals.append(round(sum(s.amount for s in sales_7d if s.created_at.date() == d), 2))
        clients_today = Client.query.filter(
            Client.registered_by == current_user.id,
            Client.created_at >= day_start,
            Client.created_at < day_end,
        ).count()
        sales_today = Sale.query.filter(
            Sale.attendant_id == current_user.id,
            Sale.created_at >= day_start,
            Sale.created_at < day_end,
        ).count()
        return day_labels, day_vals, clients_today, sales_today

    def _render_form():
        dl, dv, ct, st = _chart_data()
        return render_template('attendant/client_form.html',
                               client=None, payment_methods=PAYMENT_METHODS,
                               is_overtime=overtime,
                               commission_rate=get_commission_rate(),
                               chart_labels=dl, chart_vals=dv,
                               clients_today=ct, sales_today=st,
                               panel_options=panel_options,
                               support_options=support_options,
                               shift_end=_shift_end(),
                               now=now_br())

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Nome é obrigatório.', 'danger')
            return _render_form()

        # ── Normaliza número: remove não-dígitos e strip do prefixo 55 nacional ──
        def _norm_phone(raw):
            digits = ''.join(c for c in (raw or '') if c.isdigit())
            if len(digits) == 13 and digits.startswith('55'):
                digits = digits[2:]  # remove DDI 55
            return digits or None

        phone_raw    = request.form.get('phone', '').strip()
        whatsapp_raw = request.form.get('whatsapp', '').strip()
        phone_norm    = _norm_phone(phone_raw)
        whatsapp_norm = _norm_phone(whatsapp_raw)

        # ── Detectar cliente duplicado (nome similar OU telefone igual) ──────────
        dup_client = None
        # por nome (case-insensitive, ignora espaços extras)
        name_lower = name.lower()
        for c in Client.query.all():
            if c.name.lower() == name_lower:
                dup_client = c
                break
        # por telefone/whatsapp (se encontrar número igual)
        if not dup_client and (phone_norm or whatsapp_norm):
            for c in Client.query.all():
                c_phone = _norm_phone(c.phone)
                c_wa    = _norm_phone(c.whatsapp)
                if phone_norm and phone_norm in (c_phone, c_wa):
                    dup_client = c; break
                if whatsapp_norm and whatsapp_norm in (c_phone, c_wa):
                    dup_client = c; break

        if dup_client:
            flash(
                f'Cliente duplicado! "{dup_client.name}" já está cadastrado '
                f'(ID #{dup_client.id}, registrado por '
                f'{dup_client.registered_by_user.name.split()[0] if dup_client.registered_by_user else "outro atendente"}).',
                'danger'
            )
            return _render_form()

        client = Client(
            name=name,
            phone=phone_raw or None,
            whatsapp=whatsapp_raw or None,
            email=request.form.get('email', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            panel_name=request.form.get('panel_name', '').strip() or None,
            support_type=request.form.get('support_type', '').strip() or None,
            registered_by=current_user.id
        )
        db.session.add(client)
        db.session.flush()

        amount_str     = request.form.get('amount', '').strip().replace(',', '.')
        payment_method = request.form.get('payment_method', '').strip()
        description    = request.form.get('description', '').strip() or None

        # ── Se preencheu valor, valida tudo ANTES de salvar qualquer coisa ────────
        if amount_str:
            # 1. forma de pagamento obrigatória
            if not payment_method:
                db.session.rollback()
                flash('Selecione a forma de pagamento para registrar a venda.', 'danger')
                return _render_form()

            # 2. valor numérico válido
            try:
                base_amount = float(amount_str)
                screens     = int(request.form.get('screens', 1) or 1)
                adjustment  = float(request.form.get('adjustment', 0) or 0)
                amount      = round(base_amount + adjustment, 2)
                if amount <= 0:
                    raise ValueError('Valor zero')
            except ValueError:
                db.session.rollback()
                flash('Valor da venda inválido.', 'danger')
                return _render_form()

            # 3. comprovante (valida antes de salvar cliente)
            try:
                comprovante_filename, comprovante_hash, comp_dt, ocr_time = _process_comprovante()
            except ValueError as dup_err:
                db.session.rollback()
                flash(f'⚠️ {dup_err}', 'danger')
                return _render_form()

            # Tudo validado — salva cliente e venda juntos
            # Prioridade: formulário > OCR do comprovante > EXIF > hora do servidor
            form_time     = request.form.get('comprovante_time', '').strip()
            sale_overtime = _is_overtime_for_sale(comp_dt, form_time, ocr_time)
            if sale_overtime:
                commission_rate = 20.0
            else:
                target = current_user.monthly_sales_target or 700
                commission_rate = progressive_rate(get_month_sales_count(current_user.id), target)
            commission_amount = round(amount * commission_rate / 100, 2)
            sale = Sale(
                attendant_id=current_user.id,
                client_id=client.id,
                amount=amount,
                payment_method=payment_method,
                commission_rate=commission_rate,
                commission_amount=commission_amount,
                description=description,
                comprovante_filename=comprovante_filename,
                comprovante_hash=comprovante_hash,
                is_overtime=sale_overtime,
                screens=screens,
                adjustment=adjustment,
            )
            db.session.add(sale)
            db.session.commit()
            flash(f'Cliente {name} cadastrado! Venda de R$ {amount:.2f} registrada. '
                  f'Comissão: R$ {commission_amount:.2f} ({commission_rate:.0f}%)', 'success')
            return redirect(url_for('attendant.dashboard'))

        # Sem valor de venda — apenas salva o cliente
        db.session.commit()
        flash(f'Cliente {name} cadastrado com sucesso!', 'success')
        return redirect(url_for('attendant.dashboard'))

    return _render_form()


@attendant_bp.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@attendant_required
def edit_client(id):
    client = Client.query.get_or_404(id)
    if request.method == 'POST':
        client.name         = request.form.get('name', '').strip()
        client.phone        = request.form.get('phone', '').strip() or None
        client.whatsapp     = request.form.get('whatsapp', '').strip() or None
        client.email        = request.form.get('email', '').strip() or None
        client.notes        = request.form.get('notes', '').strip() or None
        client.panel_name   = request.form.get('panel_name', '').strip() or None
        client.support_type = request.form.get('support_type', '').strip() or None
        db.session.commit()
        flash('Cliente atualizado!', 'success')
        return redirect(url_for('attendant.clients'))
    return render_template('attendant/client_form.html', client=client,
                           panel_options=PANEL_OPTIONS, support_options=SUPPORT_OPTIONS,
                           shift_end=_shift_end(), now=now_br(),
                           is_overtime=is_overtime_now(),
                           commission_rate=get_commission_rate(),
                           payment_methods=PAYMENT_METHODS)


@attendant_bp.route('/clientes/<int:id>/painel-suporte', methods=['POST'])
@login_required
@attendant_required
def quick_edit_panel(id):
    client = Client.query.get_or_404(id)
    client.panel_name   = request.form.get('panel_name', '').strip() or None
    client.support_type = request.form.get('support_type', '').strip() or None
    db.session.commit()
    return redirect(url_for('attendant.clients',
                            q=request.args.get('q', ''),
                            panel=request.args.get('panel', ''),
                            support=request.args.get('support', '')))


# ── Vendas ─────────────────────────────────────────────────────────────────────

@attendant_bp.route('/vendas')
@login_required
@attendant_required
def sales():
    page = request.args.get('page', 1, type=int)
    sales_list = Sale.query.filter_by(attendant_id=current_user.id).order_by(Sale.created_at.desc()).paginate(page=page, per_page=20)
    return render_template('attendant/sales.html', sales=sales_list, payment_methods=PAYMENT_METHODS)


@attendant_bp.route('/vendas/nova', methods=['GET', 'POST'])
@login_required
@attendant_required
def new_sale():
    overtime = is_overtime_now()

    if overtime:
        today = today_br()
        day_start = datetime(today.year, today.month, today.day)
        approved = OvertimeRequest.query.filter(
            OvertimeRequest.user_id == current_user.id,
            OvertimeRequest.requested_at >= day_start,
            OvertimeRequest.requested_at < day_start + timedelta(days=1),
            OvertimeRequest.status == 'approved'
        ).first()
        if not approved:
            flash(f'Fora do horário comercial (08h–{_shift_end():02d}h). Solicite aprovação de hora extra para registrar vendas.', 'warning')
            return redirect(url_for('attendant.dashboard'))

    clients_list = Client.query.filter_by(registered_by=current_user.id).order_by(Client.name).all()

    if request.method == 'POST':
        amount_str = request.form.get('amount', '').strip().replace(',', '.')
        payment_method = request.form.get('payment_method', '')
        client_id = request.form.get('client_id') or None
        client_name_manual = request.form.get('client_name_manual', '').strip() or None
        description = request.form.get('description', '').strip() or None

        if not amount_str or not payment_method:
            flash('Valor e forma de pagamento são obrigatórios.', 'danger')
            cur_rate = get_commission_rate()
            return render_template('attendant/sale_form.html', clients=clients_list,
                                   is_overtime=overtime, payment_methods=PAYMENT_METHODS,
                                   commission_rate=cur_rate, shift_end=_shift_end(),
                                   now=now_br())
        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash('Valor inválido.', 'danger')
            cur_rate = get_commission_rate()
            return render_template('attendant/sale_form.html', clients=clients_list,
                                   is_overtime=overtime, payment_methods=PAYMENT_METHODS,
                                   commission_rate=cur_rate, shift_end=_shift_end(), now=now_br())

        screens    = int(request.form.get('screens', 1) or 1)
        adjustment = float(request.form.get('adjustment', 0) or 0)
        amount = round(amount + adjustment, 2)
        commission_rate = get_commission_rate(get_month_sales_count(current_user.id))
        commission_amount = round(amount * (commission_rate / 100), 2)

        try:
            comprovante_filename, comprovante_hash, comp_dt, ocr_time = _process_comprovante()
        except ValueError as dup_err:
            flash(f'⚠️ {dup_err}', 'danger')
            cur_rate = get_commission_rate()
            return render_template('attendant/sale_form.html', clients=clients_list,
                                   is_overtime=overtime, payment_methods=PAYMENT_METHODS,
                                   commission_rate=cur_rate, shift_end=_shift_end(), now=now_br())

        form_time     = request.form.get('comprovante_time', '').strip()
        sale_overtime = _is_overtime_for_sale(comp_dt, form_time, ocr_time)
        if sale_overtime:
            commission_rate   = 20.0
        else:
            target = current_user.monthly_sales_target or 700
            commission_rate = progressive_rate(get_month_sales_count(current_user.id), target)
        commission_amount = round(amount * commission_rate / 100, 2)

        sale = Sale(
            attendant_id=current_user.id,
            client_id=int(client_id) if client_id else None,
            client_name_manual=client_name_manual,
            amount=amount,
            payment_method=payment_method,
            commission_rate=commission_rate,
            commission_amount=commission_amount,
            description=description,
            comprovante_filename=comprovante_filename,
            comprovante_hash=comprovante_hash,
            is_overtime=sale_overtime,
            screens=screens,
            adjustment=adjustment,
        )
        db.session.add(sale)
        db.session.flush()
        log_action('sale_create', f'Venda registrada: R$ {amount:.2f} ({payment_method})', 'Sale', sale.id)
        db.session.commit()

        flash(f'Venda de R$ {amount:.2f} registrada! Comissão: R$ {commission_amount:.2f} ({commission_rate:.0f}%)', 'success')
        return redirect(url_for('attendant.sales'))

    cur_rate = get_commission_rate()
    return render_template('attendant/sale_form.html', clients=clients_list,
                           is_overtime=overtime, payment_methods=PAYMENT_METHODS,
                           commission_rate=cur_rate, shift_end=_shift_end(),
                           now=now_br())


# ── Comissões do atendente ─────────────────────────────────────────────────────

@attendant_bp.route('/comissoes')
@login_required
@attendant_required
def my_commissions():
    sales = Sale.query.filter_by(attendant_id=current_user.id).all()
    months: dict = {}
    for s in sales:
        k = (s.created_at.year, s.created_at.month)
        if k not in months:
            months[k] = {'earned': 0.0, 'sales': 0}
        months[k]['earned'] += s.commission_amount
        months[k]['sales']  += 1

    payments_raw = CommissionPayment.query.filter_by(attendant_id=current_user.id).all()
    paid_map = {}
    for p in payments_raw:
        k = (p.year, p.month)
        paid_map[k] = paid_map.get(k, 0.0) + p.amount

    month_names = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    data = []
    for (yr, mo), d in sorted(months.items(), reverse=True):
        paid = round(paid_map.get((yr, mo), 0.0), 2)
        data.append({
            'year': yr, 'month': mo,
            'label': f"{month_names[mo-1]}/{str(yr)[2:]}",
            'earned': round(d['earned'], 2),
            'sales':  d['sales'],
            'paid':   paid,
            'balance': round(d['earned'] - paid, 2),
        })

    return render_template('attendant/commissions.html',
        data=data,
        total_earned=round(sum(d['earned'] for d in data), 2),
        total_paid=round(sum(d['paid'] for d in data), 2),
        total_balance=round(sum(d['balance'] for d in data), 2),
    )


# ── API Preços (para quick-select no formulário) ───────────────────────────────

@attendant_bp.route('/api/precos')
@login_required
@attendant_required
def api_prices():
    items = PriceItem.query.filter_by(is_active=True).order_by(PriceItem.price).all()
    return _jsonify([{'id': i.id, 'name': i.name, 'price': i.price,
                      'description': i.description or '',
                      'screens': i.screens or 1,
                      'period_label': i.period_label or ''} for i in items])
