from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from extensions import limiter
from models import User

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.can_access_admin():
            return redirect(url_for('admin.dashboard'))
        if current_user.is_financial():
            return redirect(url_for('financial.index'))
        return redirect(url_for('attendant.dashboard'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit('10 per minute', methods=['POST'], error_message='Muitas tentativas de login. Aguarde 1 minuto.')
def login():
    if current_user.is_authenticated:
        return redirect(url_for('auth.index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()

        if user and user.check_password(password):
            if not user.is_active:
                flash('Sua conta está desativada. Contate o administrador.', 'danger')
                return render_template('login.html')
            login_user(user, remember=False)
            if user.can_access_admin():
                return redirect(url_for('admin.dashboard'))
            if user.is_financial():
                return redirect(url_for('financial.index'))
            return redirect(url_for('attendant.dashboard'))
        else:
            flash('Usuário ou senha incorretos.', 'danger')

    return render_template('login.html')


@auth_bp.route('/trocar-senha', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        current_pw  = request.form.get('current_password', '')
        new_pw      = request.form.get('new_password', '')
        confirm_pw  = request.form.get('confirm_password', '')

        if not current_user.check_password(current_pw):
            flash('Senha atual incorreta.', 'danger')
        elif len(new_pw) < 6:
            flash('A nova senha deve ter pelo menos 6 caracteres.', 'danger')
        elif new_pw != confirm_pw:
            flash('As senhas não coincidem.', 'danger')
        else:
            from models import db
            current_user.set_password(new_pw)
            db.session.commit()
            flash('Senha alterada com sucesso!', 'success')
            return redirect(url_for('auth.index'))

    return render_template('change_password.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logout realizado com sucesso.', 'success')
    return redirect(url_for('auth.login'))
