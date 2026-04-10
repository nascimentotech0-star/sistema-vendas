import json
import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
try:
    from flask_login import login_required, current_user
except Exception:
    pass


def _acai_admin_ok():
    return session.get('acai_admin_ok') is True


def _acai_admin_gate():
    """Redireciona para login da açaídeira se não autenticado."""
    return redirect(url_for('cardapio.admin_login', next=request.url))
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

_STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'static')
_VOLUME_DIR = os.path.join(_STATIC_DIR, 'uploads')

# Em produção os JSONs ficam no volume (persistem entre deploys)
# Em local ficam na pasta static (comportamento original)
def _data_file():
    vol = os.path.join(_VOLUME_DIR, 'cardapio_complementos.json')
    if os.path.exists(_VOLUME_DIR) and os.access(_VOLUME_DIR, os.W_OK):
        if not os.path.exists(vol):
            # primeira vez: copia o arquivo estático para o volume
            src = os.path.join(_STATIC_DIR, 'cardapio_complementos.json')
            if os.path.exists(src):
                import shutil
                shutil.copy2(src, vol)
        return vol
    return os.path.join(_STATIC_DIR, 'cardapio_complementos.json')

DATA_FILE = None  # resolvido dinamicamente via _data_file()


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
        "frutas": [
            {"nome": "Morango", "ativo": True, "preco": 3.00},
            {"nome": "Kiwi",    "ativo": True, "preco": 3.00},
            {"nome": "Abacaxi", "ativo": True, "preco": 2.50},
            {"nome": "Manga",   "ativo": True, "preco": 2.50},
            {"nome": "Uva",     "ativo": True, "preco": 2.50},
        ],
        "adicionais": [
            {"nome": "Fini",     "ativo": True, "preco": 2.00},
            {"nome": "Bombom",   "ativo": True, "preco": 2.00},
            {"nome": "M&M",      "ativo": True, "preco": 2.00},
        ],
        "caldas": [
            {"nome": "Leite Condensado", "ativo": True, "preco": 0.00},
            {"nome": "Nutella",          "ativo": True, "preco": 3.50},
            {"nome": "Doce de Leite",    "ativo": True, "preco": 2.00},
        ],
        "sabores": [],
    }


def _gestao_file():
    vol = os.path.join(_VOLUME_DIR, 'acaideira_gestao.json')
    if os.path.exists(_VOLUME_DIR) and os.access(_VOLUME_DIR, os.W_OK):
        if not os.path.exists(vol):
            src = os.path.join(_STATIC_DIR, 'acaideira_gestao.json')
            if os.path.exists(src):
                import shutil
                shutil.copy2(src, vol)
        return vol
    return os.path.join(_STATIC_DIR, 'acaideira_gestao.json')


def _load():
    path = _data_file()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
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
    with open(_data_file(), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_gestao():
    path = _gestao_file()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_gestao(data):
    with open(_gestao_file(), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _loja_esta_aberta(g):
    """Retorna True se a loja está aberta agora (automático por horário, com override de emergência)."""
    from datetime import datetime
    if g.get('forcar_fechado'):
        return False
    abertura   = g.get('horario_abertura',  '14:00')
    fechamento = g.get('horario_fechamento', '22:00')
    agora = datetime.now().strftime('%H:%M')
    return abertura <= agora < fechamento


# ── Login / Logout admin da açaídeira ────────────────────────────────────────

@cardapio_bp.route('/cardapio/admin/login', methods=['GET', 'POST'])
def admin_login():
    if _acai_admin_ok():
        return redirect(request.args.get('next') or url_for('cardapio.gestao'))

    g = _load_gestao()
    pin_correto = str(g.get('acai_admin_pin', '0000'))

    if request.method == 'POST':
        if request.form.get('pin', '') == pin_correto:
            session['acai_admin_ok'] = True
            next_url = request.form.get('next') or url_for('cardapio.gestao')
            return redirect(next_url)
        flash('PIN incorreto.', 'danger')

    return render_template('cardapio/admin_login.html',
                           next=request.args.get('next', ''))


@cardapio_bp.route('/cardapio/admin/sair')
def admin_sair():
    session.pop('acai_admin_ok', None)
    return redirect(url_for('cardapio.index'))


@cardapio_bp.route('/cardapio/gestao', methods=['GET', 'POST'])
def gestao():
    if not _acai_admin_ok():
        return _acai_admin_gate()

    g = _load_gestao()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_admin_pin':
            novo_pin = request.form.get('acai_admin_pin', '').strip()
            if novo_pin.isdigit() and 4 <= len(novo_pin) <= 8:
                g['acai_admin_pin'] = novo_pin
                _save_gestao(g)
                session.pop('acai_admin_ok', None)  # força novo login com PIN novo
                flash('PIN do admin atualizado! Faça login novamente.', 'success')
                return redirect(url_for('cardapio.admin_login'))
            else:
                flash('PIN deve ter entre 4 e 8 dígitos numéricos.', 'danger')

        elif action == 'update_pin_fidelidade':
            novo_pin = request.form.get('fidelidade_pin', '').strip()
            if novo_pin.isdigit() and 4 <= len(novo_pin) <= 8:
                g['fidelidade_pin'] = novo_pin
                _save_gestao(g)
                flash('PIN do carimbo atualizado!', 'success')
            else:
                flash('PIN deve ter entre 4 e 8 dígitos numéricos.', 'danger')

        elif action == 'update_config':
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

        # ── Posição imagem de combo ──────────────────────────────────────────
        elif action == 'update_combo_pos':
            cid     = request.form.get('combo_id', '')
            img_pos = request.form.get('img_pos', '50% 50%').strip() or '50% 50%'
            for c in g.get('combos', []):
                if c['id'] == cid:
                    c['img_pos'] = img_pos
                    flash(f'Posição do combo "{c["nome"]}" salva!', 'success')
                    break
            _save_gestao(g)

        # ── Upload imagem de combo ───────────────────────────────────────────
        elif action == 'upload_combo_img':
            cid  = request.form.get('combo_id', '')
            file = request.files.get('imagem')
            if file and _allowed_img(file.filename):
                path = _save_item_img(file, 'combos')
                for c in g.get('combos', []):
                    if c['id'] == cid:
                        c['img'] = path
                        flash(f'Imagem do combo "{c["nome"]}" atualizada!', 'success')
                        break
                _save_gestao(g)
            else:
                flash('Arquivo inválido. Use JPG, PNG ou WebP.', 'danger')

        # ── Posição imagem de especial ───────────────────────────────────────
        elif action == 'update_especial_pos':
            eid     = request.form.get('especial_id', '')
            img_pos = request.form.get('img_pos', '50% 50%').strip() or '50% 50%'
            especiais_default = [
                {"id": "marmita",  "nome": "Marmita de Açaí",       "desc": "Serve 2 pessoas 💜",       "preco": 28.70, "emoji": "🥡", "ativo": True},
                {"id": "garrafa",  "nome": "Açaí na Garrafa 500ml", "desc": "Cremoso, para o caminho",  "preco": 25.99, "emoji": "🥤", "ativo": True},
                {"id": "ovo250",   "nome": "Ovo de Páscoa 250g",     "desc": "Especial de Páscoa 🐣",   "preco": 28.70, "emoji": "🥚", "ativo": True},
                {"id": "ovo350",   "nome": "Ovo de Páscoa 350g",     "desc": "Especial de Páscoa 🐣",   "preco": 31.90, "emoji": "🥚", "ativo": True},
            ]
            if 'especiais' not in g:
                g['especiais'] = especiais_default
            for e in g['especiais']:
                if e['id'] == eid:
                    e['img_pos'] = img_pos
                    flash(f'Posição de "{e["nome"]}" salva!', 'success')
                    break
            _save_gestao(g)

        # ── Upload imagem de especial ────────────────────────────────────────
        elif action == 'upload_especial_img':
            eid  = request.form.get('especial_id', '')
            file = request.files.get('imagem')
            especiais_default = [
                {"id": "marmita",  "nome": "Marmita de Açaí",       "desc": "Serve 2 pessoas 💜",       "preco": 28.70, "emoji": "🥡", "ativo": True},
                {"id": "garrafa",  "nome": "Açaí na Garrafa 500ml", "desc": "Cremoso, para o caminho",  "preco": 25.99, "emoji": "🥤", "ativo": True},
                {"id": "ovo250",   "nome": "Ovo de Páscoa 250g",     "desc": "Especial de Páscoa 🐣",   "preco": 28.70, "emoji": "🥚", "ativo": True},
                {"id": "ovo350",   "nome": "Ovo de Páscoa 350g",     "desc": "Especial de Páscoa 🐣",   "preco": 31.90, "emoji": "🥚", "ativo": True},
            ]
            if 'especiais' not in g:
                g['especiais'] = especiais_default
            if file and _allowed_img(file.filename):
                path = _save_item_img(file, 'especiais')
                for e in g['especiais']:
                    if e['id'] == eid:
                        e['img'] = path
                        flash(f'Imagem de "{e["nome"]}" atualizada!', 'success')
                        break
                _save_gestao(g)
            else:
                flash('Arquivo inválido. Use JPG, PNG ou WebP.', 'danger')

        # ── Toggle especial ──────────────────────────────────────────────────
        elif action == 'toggle_especial':
            eid = request.form.get('especial_id', '')
            especiais_default = [
                {"id": "marmita",  "nome": "Marmita de Açaí",       "desc": "Serve 2 pessoas 💜",       "preco": 28.70, "emoji": "🥡", "ativo": True},
                {"id": "garrafa",  "nome": "Açaí na Garrafa 500ml", "desc": "Cremoso, para o caminho",  "preco": 25.99, "emoji": "🥤", "ativo": True},
                {"id": "ovo250",   "nome": "Ovo de Páscoa 250g",     "desc": "Especial de Páscoa 🐣",   "preco": 28.70, "emoji": "🥚", "ativo": True},
                {"id": "ovo350",   "nome": "Ovo de Páscoa 350g",     "desc": "Especial de Páscoa 🐣",   "preco": 31.90, "emoji": "🥚", "ativo": True},
            ]
            if 'especiais' not in g:
                g['especiais'] = especiais_default
            for e in g['especiais']:
                if e['id'] == eid:
                    e['ativo'] = not e.get('ativo', True)
                    flash(f'"{e["nome"]}" {"ativado" if e["ativo"] else "desativado"}.', 'info')
                    break
            _save_gestao(g)

        # ── Atualizar preço especial ─────────────────────────────────────────
        elif action == 'update_especial_preco':
            eid = request.form.get('especial_id', '')
            try:
                novo_preco = float(request.form.get('preco', '0').replace(',', '.'))
            except ValueError:
                novo_preco = 0.0
            especiais_default = [
                {"id": "marmita",  "nome": "Marmita de Açaí",       "desc": "Serve 2 pessoas 💜",       "preco": 28.70, "emoji": "🥡", "ativo": True},
                {"id": "garrafa",  "nome": "Açaí na Garrafa 500ml", "desc": "Cremoso, para o caminho",  "preco": 25.99, "emoji": "🥤", "ativo": True},
                {"id": "ovo250",   "nome": "Ovo de Páscoa 250g",     "desc": "Especial de Páscoa 🐣",   "preco": 28.70, "emoji": "🥚", "ativo": True},
                {"id": "ovo350",   "nome": "Ovo de Páscoa 350g",     "desc": "Especial de Páscoa 🐣",   "preco": 31.90, "emoji": "🥚", "ativo": True},
            ]
            if 'especiais' not in g:
                g['especiais'] = especiais_default
            for e in g['especiais']:
                if e['id'] == eid:
                    e['preco'] = novo_preco
                    flash(f'Preço de "{e["nome"]}" atualizado!', 'success')
                    break
            _save_gestao(g)

        return redirect(url_for('cardapio.gestao'))

    return render_template('cardapio/gestao.html', g=g)


# ── Calculadora de precificação (admin) ──────────────────────────────────────

@cardapio_bp.route('/cardapio/calculadora')
def calculadora():
    if not _acai_admin_ok():
        return _acai_admin_gate()
    return render_template('cardapio/calculadora.html')


# ── API: status da loja (aberta/fechada) ─────────────────────────────────────

@cardapio_bp.route('/cardapio/api/status_loja')
def api_status_loja():
    from flask import jsonify
    g = _load_gestao()
    aberta = _loja_esta_aberta(g)
    return jsonify({
        'aberta':        aberta,
        'forcar_fechado': g.get('forcar_fechado', False),
        'abertura':      g.get('horario_abertura',  '14:00'),
        'fechamento':    g.get('horario_fechamento', '22:00'),
    })


# ── Toggle loja (admin) ───────────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/toggle_loja', methods=['POST'])
def toggle_loja():
    if not _acai_admin_ok():
        from flask import jsonify
        return jsonify({'erro': 'não autorizado'}), 403
    from flask import jsonify
    g = _load_gestao()
    # Toggle do override de emergência (não altera o automático por horário)
    g['forcar_fechado'] = not g.get('forcar_fechado', False)
    _save_gestao(g)
    aberta = _loja_esta_aberta(g)
    estado = 'aberta' if aberta else 'fechada'
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or \
       request.headers.get('Accept', '').startswith('application/json'):
        return jsonify({'aberta': aberta, 'forcar_fechado': g['forcar_fechado'], 'estado': estado})
    flash(f'Loja {estado}!', 'success' if aberta else 'warning')
    next_url = request.form.get('next') or url_for('cardapio.status_loja')
    return redirect(next_url)


# ── Página de status (para a mãe) ────────────────────────────────────────────

@cardapio_bp.route('/cardapio/status')
def status_loja():
    if not _acai_admin_ok():
        return _acai_admin_gate()
    g = _load_gestao()
    return render_template('cardapio/status_loja.html', g=g,
                           loja_aberta=_loja_esta_aberta(g))


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
        'frutas':          public.get('frutas', []),
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
                           loja_aberta=_loja_esta_aberta(g),
                           horario_abertura=g.get('horario_abertura', '14:00'),
                           horario_fechamento=g.get('horario_fechamento', '22:00'))


# ── Painel admin ─────────────────────────────────────────────────────────────

@cardapio_bp.route('/cardapio/gerenciar', methods=['GET', 'POST'])
def gerenciar():
    if not _acai_admin_ok():
        return _acai_admin_gate()

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

        # ── Salvar posição da imagem do copo ────────────────────────────────
        if action == 'update_copo_pos':
            tamanho = request.form.get('tamanho', '')
            img_pos = request.form.get('img_pos', '50% 50%').strip() or '50% 50%'
            for copo in data.get('copos', []):
                if copo['tamanho'] == tamanho:
                    copo['img_pos'] = img_pos
                    break
            _save(data)
            flash(f'Posição do copo {tamanho} salva!', 'success')
            return redirect(url_for('cardapio.gerenciar'))

        # ── Upload imagem do copo (sem category) ────────────────────────────
        if action == 'upload_copo_img':
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

        return redirect(url_for('cardapio.gerenciar'))

    g = _load_gestao()
    return render_template('cardapio/gerenciar.html', data=data,
                           g_gestao=g,
                           loja_aberta=_loja_esta_aberta(g))


# ── Carimbar — página isolada para a loja (sem login do sistema) ─────────────

@cardapio_bp.route('/cardapio/carimbar', methods=['GET', 'POST'])
def carimbar():
    """Página de carimbo isolada — protegida por PIN, sem conexão com o sistema de funcionários."""
    g   = _load_gestao()
    pin = str(g.get('fidelidade_pin', '1234'))

    tel = request.args.get('tel', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')

    if request.method == 'POST':
        action = request.form.get('action', '')

        # Verificar PIN
        if action == 'verificar_pin':
            if request.form.get('pin', '') == pin:
                session['fid_autenticado'] = True
                return redirect(url_for('cardapio.carimbar', tel=tel))
            flash('PIN incorreto.', 'danger')
            return render_template('cardapio/carimbar.html', tel=tel, pin_validado=False, cliente=None)

        # Carimbar — só se PIN já validado na session
        if action == 'carimbar' and session.get('fid_autenticado'):
            tel_form = request.form.get('tel', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            obs      = request.form.get('obs', '').strip() or None
            cliente  = FidelidadeCliente.query.filter_by(telefone=tel_form).first()
            if cliente:
                db.session.add(FidelidadePedido(cliente_id=cliente.id, is_free=False, obs=obs))
                db.session.commit()
                flash(f'✅ {cliente.nome} carimbado! {cliente.selos_atuais}/10 selos.', 'success')
            else:
                flash('Cliente não encontrado nesse telefone.', 'warning')
            return redirect(url_for('cardapio.carimbar', tel=tel_form))

        # Resgatar free — só se PIN validado
        if action == 'resgatar' and session.get('fid_autenticado'):
            tel_form = request.form.get('tel', '').replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
            cliente  = FidelidadeCliente.query.filter_by(telefone=tel_form).first()
            if cliente and cliente.tem_free_pendente:
                db.session.add(FidelidadePedido(cliente_id=cliente.id, is_free=True, obs='Açaí grátis resgatado'))
                db.session.commit()
                flash(f'🎁 Açaí grátis de {cliente.nome} resgatado!', 'success')
            return redirect(url_for('cardapio.carimbar', tel=tel_form))

        # Sair (limpar PIN da session)
        if action == 'sair':
            session.pop('fid_autenticado', None)
            return redirect(url_for('cardapio.carimbar'))

    pin_validado = session.get('fid_autenticado', False)
    cliente = None
    if pin_validado and tel:
        cliente = FidelidadeCliente.query.filter_by(telefone=tel).first()

    return render_template('cardapio/carimbar.html', tel=tel, cliente=cliente, pin_validado=pin_validado)


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
def fidelidade_admin():
    if not _acai_admin_ok():
        return _acai_admin_gate()

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
def promocoes_admin():
    if not _acai_admin_ok():
        return _acai_admin_gate()

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
