import json
import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

ALLOWED_IMG = {'jpg', 'jpeg', 'png', 'webp', 'gif'}

def _allowed_img(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMG

def _save_item_img(file, subfolder='cardapio'):
    """Salva imagem e retorna o caminho relativo para usar no template."""
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', subfolder)
    os.makedirs(upload_dir, exist_ok=True)
    ext = file.filename.rsplit('.', 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(upload_dir, fname))
    return f"uploads/{subfolder}/{fname}"

cardapio_bp = Blueprint('cardapio', __name__)
from models import db, FidelidadeCliente, FidelidadePedido, Promocao
from datetime import date as _date

DATA_FILE = os.path.join(os.path.dirname(__file__), '..', 'static', 'cardapio_complementos.json')


def _default_data():
    return {
        "copos": [
            {"tamanho": "380ml", "nome": "Pequeno", "preco": 23.90},
            {"tamanho": "480ml", "nome": "Médio",   "preco": 30.90},
            {"tamanho": "780ml", "nome": "Grande",  "preco": 44.90},
        ],
        "acompanhamentos": [
            {"nome": "Granola",     "ativo": True, "preco": 0},
            {"nome": "Leite em Pó", "ativo": True, "preco": 0},
            {"nome": "Amendoim",    "ativo": True, "preco": 0},
            {"nome": "Paçoca",      "ativo": True, "preco": 0},
            {"nome": "Banana",      "ativo": True, "preco": 0},
        ],
        "adicionais": [
            {"nome": "Fini",     "ativo": True, "preco": 2.00},
            {"nome": "Bombom",   "ativo": True, "preco": 2.00},
            {"nome": "M&M",      "ativo": True, "preco": 2.00},
        ],
        "caldas": [
            {"nome": "Nutella",        "ativo": True, "preco": 3.50},
            {"nome": "Doce de Leite",  "ativo": True, "preco": 2.00},
        ],
        "sabores": [],
    }


def _load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            # migração: listas antigas de strings → objetos
            for key in raw:
                if key == 'copos':
                    continue
                if raw[key] and isinstance(raw[key][0], str):
                    raw[key] = [{"nome": n, "ativo": True, "preco": 0} for n in raw[key]]
                # garante campo preco em itens antigos
                for item in raw[key]:
                    if isinstance(item, dict) and 'preco' not in item:
                        item['preco'] = 0
            return raw
        except Exception:
            pass
    data = _default_data()
    _save(data)
    return data


def _save(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


GESTAO_FILE = os.path.join(os.path.dirname(__file__), '..', 'static', 'acaideira_gestao.json')


def _load_gestao():
    if os.path.exists(GESTAO_FILE):
        try:
            with open(GESTAO_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_gestao(data):
    with open(GESTAO_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@cardapio_bp.route('/cardapio/gestao', methods=['GET', 'POST'])
@login_required
def gestao():
    if not current_user.is_admin():
        return redirect(url_for('cardapio.index'))

    g = _load_gestao()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_config':
            g['ticket_medio']          = float(request.form.get('ticket_medio', 30).replace(',', '.'))
            g['pedidos_dia_estimado']  = int(request.form.get('pedidos_dia', 20))
            g['meta_caixa']            = float(request.form.get('meta_caixa', 10000).replace(',', '.'))
            g['caixa_atual']           = float(request.form.get('caixa_atual', 0).replace(',', '.'))
            g['custos_variaveis']['cmv_pct']               = float(request.form.get('cmv_pct', 40))
            g['custos_variaveis']['embalagem_por_pedido']  = float(request.form.get('embalagem', 1.5).replace(',', '.'))
            g['custos_variaveis']['taxa_entrega_por_pedido'] = float(request.form.get('taxa_entrega', 0).replace(',', '.'))
            _save_gestao(g)
            flash('Configurações salvas!', 'success')

        elif action == 'add_custo':
            nome  = request.form.get('custo_nome', '').strip()
            valor = float(request.form.get('custo_valor', 0).replace(',', '.'))
            if nome and valor > 0:
                g['custos_fixos'].append({'nome': nome, 'valor': valor})
                _save_gestao(g)
                flash(f'"{nome}" adicionado!', 'success')

        elif action == 'update_custo':
            idx   = int(request.form.get('idx', -1))
            valor = float(request.form.get('valor', 0).replace(',', '.'))
            if 0 <= idx < len(g['custos_fixos']):
                g['custos_fixos'][idx]['valor'] = valor
                _save_gestao(g)
                flash('Custo atualizado!', 'success')

        elif action == 'remove_custo':
            idx = int(request.form.get('idx', -1))
            if 0 <= idx < len(g['custos_fixos']):
                nome = g['custos_fixos'].pop(idx)['nome']
                _save_gestao(g)
                flash(f'"{nome}" removido.', 'info')

        elif action == 'add_pedido':
            data_ped = request.form.get('data_ped', '')
            desc     = request.form.get('desc', '').strip()
            valor    = float(request.form.get('valor_ped', 0).replace(',', '.'))
            if desc and valor > 0:
                g.setdefault('pedidos_fornecedor', []).append({
                    'data': data_ped, 'descricao': desc, 'valor': valor
                })
                _save_gestao(g)
                flash('Pedido ao fornecedor registrado!', 'success')

        elif action == 'remove_pedido':
            idx = int(request.form.get('idx', -1))
            if 0 <= idx < len(g.get('pedidos_fornecedor', [])):
                g['pedidos_fornecedor'].pop(idx)
                _save_gestao(g)
                flash('Pedido removido.', 'info')

        # ── COMBOS ───────────────────────────────────────────────────────────
        elif action == 'add_combo':
            nome_c  = request.form.get('combo_nome', '').strip()
            desc_c  = request.form.get('combo_desc', '').strip()
            itens_c = [i.strip() for i in request.form.get('combo_itens', '').split('\n') if i.strip()]
            emoji_c = request.form.get('combo_emoji', '🎯').strip() or '🎯'
            try:
                preco_orig = float(request.form.get('combo_preco_orig', '0').replace(',', '.'))
                preco_c    = float(request.form.get('combo_preco', '0').replace(',', '.'))
            except ValueError:
                preco_orig = preco_c = 0.0
            if nome_c and preco_c > 0:
                combo = {
                    'id': f"combo_{uuid.uuid4().hex[:8]}",
                    'nome': nome_c, 'desc': desc_c, 'itens': itens_c,
                    'emoji': emoji_c, 'preco_original': preco_orig,
                    'preco': preco_c, 'ativo': True,
                }
                g.setdefault('combos', []).append(combo)
                _save_gestao(g)
                flash(f'Combo "{nome_c}" criado!', 'success')

        elif action == 'toggle_combo':
            cid = request.form.get('combo_id', '')
            for c in g.get('combos', []):
                if c['id'] == cid:
                    c['ativo'] = not c.get('ativo', True)
                    flash(f'"{c["nome"]}" {"ativado" if c["ativo"] else "desativado"}.', 'info')
                    break
            _save_gestao(g)

        elif action == 'remove_combo':
            cid = request.form.get('combo_id', '')
            antes = len(g.get('combos', []))
            g['combos'] = [c for c in g.get('combos', []) if c['id'] != cid]
            if len(g.get('combos', [])) < antes:
                flash('Combo removido.', 'info')
            _save_gestao(g)

        elif action == 'update_combo_preco':
            cid = request.form.get('combo_id', '')
            try:
                novo_preco = float(request.form.get('combo_preco', '0').replace(',', '.'))
            except ValueError:
                novo_preco = 0.0
            for c in g.get('combos', []):
                if c['id'] == cid:
                    c['preco'] = novo_preco
                    flash(f'Preço do combo "{c["nome"]}" atualizado!', 'success')
                    break
            _save_gestao(g)

        return redirect(url_for('cardapio.gestao'))

    return render_template('cardapio/gestao.html', g=g)


# ── Calculadora de precificação (admin) ──────────────────────────────────────

@cardapio_bp.route('/cardapio/calculadora')
@login_required
def calculadora():
    if not current_user.is_admin():
        return redirect(url_for('cardapio.index'))
    return render_template('cardapio/calculadora.html')


# ── API: status da loja (aberta/fechada) ─────────────────────────────────────

@cardapio_bp.route('/cardapio/api/status_loja')
def api_status_loja():
    from flask import jsonify
    g = _load_gestao()
    return jsonify({
        'aberta':   g.get('loja_aberta', False),
        'abertura': g.get('horario_abertura', '14:00'),
        'fechamento': g.get('horario_fechamento', '22:00'),
    })


# ── Toggle loja (admin) ───────────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/toggle_loja', methods=['POST'])
@login_required
def toggle_loja():
    from flask import jsonify
    g = _load_gestao()
    g['loja_aberta'] = not g.get('loja_aberta', False)
    _save_gestao(g)
    estado = 'aberta' if g['loja_aberta'] else 'fechada'
    # Responde JSON se for chamada AJAX, senão redireciona
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
       request.headers.get('Accept', '').startswith('application/json'):
        return jsonify({'aberta': g['loja_aberta'], 'estado': estado})
    flash(f'Loja {estado}!', 'success' if g['loja_aberta'] else 'warning')
    next_url = request.form.get('next') or url_for('cardapio.status_loja')
    return redirect(next_url)


# ── Página de status (para a mãe) ────────────────────────────────────────────

@cardapio_bp.route('/cardapio/status')
@login_required
def status_loja():
    g = _load_gestao()
    return render_template('cardapio/status_loja.html', g=g)


# ── Cardápio público ─────────────────────────────────────────────────────────

@cardapio_bp.route('/cardapio')
def index():
    import json as _json
    raw  = _load()
    g    = _load_gestao()
    public = {}
    for k, v in raw.items():
        if k == 'copos':
            public[k] = v
        else:
            public[k] = [i for i in v if i.get('ativo', True)]

    especiais_default = [
        {"id": "marmita",  "nome": "Marmita de Açaí",       "desc": "Serve 2 pessoas 💜",       "preco": 28.70, "emoji": "🥡", "ativo": True},
        {"id": "garrafa",  "nome": "Açaí na Garrafa 500ml", "desc": "Cremoso, para o caminho",  "preco": 25.99, "emoji": "🥤", "ativo": True},
        {"id": "ovo250",   "nome": "Ovo de Páscoa 250g",     "desc": "Especial de Páscoa 🐣",   "preco": 28.70, "emoji": "🥚", "ativo": True},
        {"id": "ovo350",   "nome": "Ovo de Páscoa 350g",     "desc": "Especial de Páscoa 🐣",   "preco": 31.90, "emoji": "🥚", "ativo": True},
    ]
    especiais = [e for e in g.get('especiais', especiais_default) if e.get('ativo', True)]

    data_json = _json.dumps({
        'copos':           public.get('copos', []),
        'caldas':          public.get('caldas', []),
        'acompanhamentos': public.get('acompanhamentos', []),
        'adicionais':      public.get('adicionais', []),
        'sabores':         public.get('sabores', []),
        'especiais':       especiais,
    }, ensure_ascii=False)

    combos_default = [
        {
            "id": "combo_duplo",
            "nome": "Combo Duplo",
            "desc": "2 açaís de 480ml",
            "itens": ["2x Açaí Médio 480ml"],
            "preco_original": 61.80,
            "preco": 55.00,
            "emoji": "🎯",
            "ativo": True,
        },
        {
            "id": "combo_familia",
            "nome": "Combo Família",
            "desc": "3 açaís de 380ml",
            "itens": ["3x Açaí Pequeno 380ml"],
            "preco_original": 71.70,
            "preco": 62.00,
            "emoji": "👨‍👩‍👧",
            "ativo": True,
        },
        {
            "id": "combo_marmita_garrafa",
            "nome": "Combo Especial",
            "desc": "Marmita + Garrafa 500ml",
            "itens": ["1x Marmita de Açaí", "1x Açaí na Garrafa 500ml"],
            "preco_original": 54.69,
            "preco": 49.90,
            "emoji": "⭐",
            "ativo": True,
        },
    ]
    combos = [c for c in g.get('combos', combos_default) if c.get('ativo', True)]

    data_json = _json.dumps({
        'copos':           public.get('copos', []),
        'caldas':          public.get('caldas', []),
        'acompanhamentos': public.get('acompanhamentos', []),
        'adicionais':      public.get('adicionais', []),
        'sabores':         public.get('sabores', []),
        'especiais':       especiais,
        'combos':          combos,
    }, ensure_ascii=False)

    return render_template('cardapio/index.html',
                           data=public,
                           especiais=especiais,
                           combos=combos,
                           whatsapp=g.get('whatsapp', '77998298970'),
                           data_json=data_json,
                           loja_aberta=g.get('loja_aberta', False),
                           horario_abertura=g.get('horario_abertura', '14:00'),
                           horario_fechamento=g.get('horario_fechamento', '22:00'))


# ── Painel admin ─────────────────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/gerenciar', methods=['GET', 'POST'])
@login_required
def gerenciar():
    if not current_user.is_admin():
        return redirect(url_for('cardapio.index'))

    data = _load()

    if request.method == 'POST':
        action   = request.form.get('action')
        category = request.form.get('category', '')
        nome     = request.form.get('nome', '').strip()

        # ── Atualizar preço de copo ──────────────────────────────────────────
        if action == 'update_copo':
            tamanho = request.form.get('tamanho', '')
            try:
                preco = float(request.form.get('preco', '0').replace(',', '.'))
            except ValueError:
                preco = 0.0
            for copo in data.get('copos', []):
                if copo['tamanho'] == tamanho:
                    copo['preco'] = preco
                    break
            _save(data)
            flash(f'Preço do copo {tamanho} atualizado!', 'success')
            return redirect(url_for('cardapio.gerenciar'))

        if category not in data:
            flash('Categoria inválida.', 'danger')
            return redirect(url_for('cardapio.gerenciar'))

        # ── Adicionar item ───────────────────────────────────────────────────
        if action == 'add' and nome:
            nomes = [i['nome'] for i in data[category]]
            if nome not in nomes:
                try:
                    preco = float(request.form.get('preco', '0').replace(',', '.'))
                except ValueError:
                    preco = 0.0
                data[category].append({'nome': nome, 'ativo': True, 'preco': preco})
                _save(data)
                flash(f'"{nome}" adicionado!', 'success')
            else:
                flash(f'"{nome}" já existe.', 'warning')

        # ── Remover item ─────────────────────────────────────────────────────
        elif action == 'remove' and nome:
            data[category] = [i for i in data[category] if i['nome'] != nome]
            _save(data)
            flash(f'"{nome}" removido.', 'info')

        # ── Ativar/desativar ─────────────────────────────────────────────────
        elif action == 'toggle' and nome:
            for item in data[category]:
                if item['nome'] == nome:
                    item['ativo'] = not item.get('ativo', True)
                    estado = 'ativado' if item['ativo'] else 'desativado'
                    _save(data)
                    flash(f'"{nome}" {estado}.', 'success')
                    break

        # ── Editar preço ─────────────────────────────────────────────────────
        elif action == 'update_preco' and nome:
            try:
                preco = float(request.form.get('preco', '0').replace(',', '.'))
            except ValueError:
                preco = 0.0
            for item in data[category]:
                if item['nome'] == nome:
                    item['preco'] = preco
                    _save(data)
                    flash(f'Preço de "{nome}" atualizado!', 'success')
                    break

        # ── Upload de imagem em item ─────────────────────────────────────────
        elif action == 'upload_img' and nome:
            file = request.files.get('imagem')
            if file and _allowed_img(file.filename):
                path = _save_item_img(file)
                for item in data.get(category, []):
                    if item['nome'] == nome:
                        item['img'] = path
                        _save(data)
                        flash(f'Imagem de "{nome}" atualizada!', 'success')
                        break
            else:
                flash('Arquivo inválido. Use JPG, PNG ou WebP.', 'danger')

        # ── Upload imagem do copo ────────────────────────────────────────────
        elif action == 'upload_copo_img':
            tamanho = request.form.get('tamanho', '')
            file = request.files.get('imagem')
            if file and _allowed_img(file.filename):
                path = _save_item_img(file)
                for copo in data.get('copos', []):
                    if copo['tamanho'] == tamanho:
                        copo['img'] = path
                        _save(data)
                        flash(f'Imagem do copo {tamanho} atualizada!', 'success')
                        break
            else:
                flash('Arquivo inválido. Use JPG, PNG ou WebP.', 'danger')

        return redirect(url_for('cardapio.gerenciar'))

    g = _load_gestao()
    return render_template('cardapio/gerenciar.html', data=data,
                           g_gestao=g,
                           loja_aberta=g.get('loja_aberta', False))


# ── Fidelidade — API (remember-me via localStorage) ─────────────────────────

@cardapio_bp.route('/cardapio/api/cliente/<telefone>')
def api_cliente(telefone):
    from flask import jsonify
    tel = telefone.strip().replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    c = FidelidadeCliente.query.filter_by(telefone=tel).first()
    if not c:
        return jsonify(None), 404
    return jsonify({
        'id': c.id, 'nome': c.nome, 'telefone': c.telefone,
        'selos': c.selos_atuais, 'total': c.total_pagos,
        'cartoes': c.cartoes_completos, 'free': c.tem_free_pendente,
        'proximos': c.proximos_selos, 'desconto': c.desconto_tier,
        'proximo_tier': c.proximo_tier,
        'seguidor_ig': c.seguidor_ig, 'seguidor_validado': c.seguidor_validado,
        'codigo_origem': c.codigo_origem,
    })


# ── Fidelidade — página pública ───────────────────────────────────────────────

@cardapio_bp.route('/cardapio/fidelidade', methods=['GET', 'POST'])
def fidelidade():
    cliente = None
    msg     = None

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'cadastrar':
            nome     = request.form.get('nome', '').strip()
            telefone = request.form.get('telefone', '').strip().replace(' ', '').replace('-', '')
            email    = request.form.get('email', '').strip() or None
            codigo_influencer = request.form.get('codigo_influencer', '').strip().upper() or None
            if nome and telefone:
                existente = FidelidadeCliente.query.filter_by(telefone=telefone).first()
                if existente:
                    cliente = existente
                    msg = ('info', 'Você já está cadastrado! Veja seu cartão abaixo.')
                else:
                    novo = FidelidadeCliente(nome=nome, telefone=telefone, email=email,
                                            codigo_origem=codigo_influencer)
                    db.session.add(novo)
                    # Incrementa uso do código se válido
                    if codigo_influencer:
                        promo = Promocao.query.filter(
                            db.func.upper(Promocao.codigo) == codigo_influencer
                        ).first()
                        if promo:
                            promo.usos = (promo.usos or 0) + 1
                    db.session.commit()
                    cliente = novo
                    msg = ('success', f'Bem-vindo, {nome}! Seu cartão de fidelidade foi criado.')
            else:
                msg = ('danger', 'Preencha o nome e o telefone.')

        elif action == 'consultar':
            telefone = request.form.get('telefone', '').strip().replace(' ', '').replace('-', '')
            cliente  = FidelidadeCliente.query.filter_by(telefone=telefone).first()
            if not cliente:
                msg = ('warning', 'Telefone não encontrado. Faça seu cadastro abaixo.')

    return render_template('cardapio/fidelidade.html', cliente=cliente, msg=msg)


# ── Fidelidade — painel admin ─────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/fidelidade/admin', methods=['GET', 'POST'])
@login_required
def fidelidade_admin():
    if not current_user.is_admin():
        return redirect(url_for('cardapio.index'))

    if request.method == 'POST':
        action     = request.form.get('action')
        cliente_id = request.form.get('cliente_id', type=int)

        if action == 'add_pedido' and cliente_id:
            obs = request.form.get('obs', '').strip() or None
            db.session.add(FidelidadePedido(cliente_id=cliente_id, is_free=False, obs=obs))
            db.session.commit()
            flash('Pedido registrado! Selo adicionado.', 'success')

        elif action == 'resgatar' and cliente_id:
            cliente = FidelidadeCliente.query.get(cliente_id)
            if cliente and cliente.tem_free_pendente:
                db.session.add(FidelidadePedido(cliente_id=cliente_id, is_free=True,
                                                obs='Açaí grátis resgatado'))
                db.session.commit()
                flash(f'Açaí grátis de {cliente.nome} registrado como resgatado!', 'success')
            else:
                flash('Cliente não tem resgate pendente.', 'warning')

        elif action == 'validar_ig':
            cliente = FidelidadeCliente.query.get(cliente_id)
            if cliente:
                cliente.seguidor_ig       = True
                cliente.seguidor_validado = True
                db.session.commit()
                flash(f'Follow de {cliente.nome} confirmado!', 'success')

        elif action == 'remover_pedido':
            pedido_id = request.form.get('pedido_id', type=int)
            pedido = FidelidadePedido.query.get(pedido_id)
            if pedido:
                db.session.delete(pedido)
                db.session.commit()
                flash('Pedido removido.', 'info')

        return redirect(url_for('cardapio.fidelidade_admin'))

    busca    = request.args.get('q', '').strip()
    query    = FidelidadeCliente.query
    if busca:
        query = query.filter(
            FidelidadeCliente.nome.ilike(f'%{busca}%') |
            FidelidadeCliente.telefone.ilike(f'%{busca}%')
        )
    clientes = query.order_by(FidelidadeCliente.created_at.desc()).all()
    return render_template('cardapio/fidelidade_admin.html', clientes=clientes, busca=busca)


# ── Promoções — página pública ────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/api/promocoes_destaque')
def api_promocoes_destaque():
    from flask import jsonify
    ativas = [p for p in Promocao.query.filter_by(ativa=True, destaque=True).all() if p.vigente]
    return jsonify([{'titulo': p.titulo, 'desconto_texto': p.desconto_texto} for p in ativas[:3]])


@cardapio_bp.route('/cardapio/promocoes')
def promocoes():
    ativas = [p for p in Promocao.query.filter_by(ativa=True)
                                       .order_by(Promocao.destaque.desc(), Promocao.created_at.desc())
                                       .all() if p.vigente]
    return render_template('cardapio/promocoes.html', promocoes=ativas)


# ── Promoções — painel admin ──────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/promocoes/admin', methods=['GET', 'POST'])
@login_required
def promocoes_admin():
    if not current_user.is_admin():
        return redirect(url_for('cardapio.index'))

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'criar':
            def _d(field):
                v = request.form.get(field, '').strip()
                if v:
                    try:
                        from datetime import datetime
                        return datetime.strptime(v, '%Y-%m-%d').date()
                    except Exception:
                        pass
                return None

            valor_raw = request.form.get('valor', '').replace(',', '.').strip()
            valor = float(valor_raw) if valor_raw else None

            p = Promocao(
                titulo      = request.form.get('titulo', '').strip(),
                tipo        = request.form.get('tipo', 'custom'),
                descricao   = request.form.get('descricao', '').strip() or None,
                valor       = valor,
                condicao    = request.form.get('condicao', '').strip() or None,
                codigo      = request.form.get('codigo', '').strip().upper() or None,
                destaque    = bool(request.form.get('destaque')),
                data_inicio = _d('data_inicio'),
                data_fim    = _d('data_fim'),
                ativa       = True,
            )
            db.session.add(p)
            db.session.commit()
            flash(f'Promoção "{p.titulo}" criada!', 'success')

        elif action == 'toggle':
            pid = request.form.get('pid', type=int)
            p   = Promocao.query.get(pid)
            if p:
                p.ativa = not p.ativa
                db.session.commit()
                flash(f'"{p.titulo}" {"ativada" if p.ativa else "pausada"}.', 'info')

        elif action == 'excluir':
            pid = request.form.get('pid', type=int)
            p   = Promocao.query.get(pid)
            if p:
                db.session.delete(p)
                db.session.commit()
                flash('Promoção excluída.', 'warning')

        elif action == 'toggle_destaque':
            pid = request.form.get('pid', type=int)
            p   = Promocao.query.get(pid)
            if p:
                p.destaque = not p.destaque
                db.session.commit()
                flash(f'Destaque {"ativado" if p.destaque else "removido"}.', 'info')

        return redirect(url_for('cardapio.promocoes_admin'))

    todas = Promocao.query.order_by(Promocao.created_at.desc()).all()
    return render_template('cardapio/promocoes_admin.html',
                           promocoes=todas, tipos=Promocao.TIPOS, hoje=_date.today())
