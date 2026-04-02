/**
 * NT Form Validation — validação inline para formulários
 * Uso: adicione data-nt-validate no <form>
 * Campos: required, data-min-val (número mínimo), data-phone (formato telefone)
 */
(function () {
  const COLOR_OK  = 'rgba(16,185,129,.5)';
  const COLOR_ERR = 'rgba(239,68,68,.6)';

  function showError(input, msg) {
    input.style.borderColor = COLOR_ERR;
    input.style.boxShadow   = `0 0 0 3px rgba(239,68,68,.15)`;
    let fb = input.parentElement.querySelector('.nt-feedback');
    if (!fb) {
      fb = document.createElement('div');
      fb.className = 'nt-feedback';
      fb.style.cssText = 'font-size:.74rem;margin-top:4px;color:#fca5a5;display:flex;align-items:center;gap:4px;';
      input.parentElement.appendChild(fb);
    }
    fb.innerHTML = `<i class="bi bi-exclamation-circle-fill"></i>${msg}`;
  }

  function showOk(input) {
    input.style.borderColor = COLOR_OK;
    input.style.boxShadow   = `0 0 0 3px rgba(16,185,129,.1)`;
    const fb = input.parentElement.querySelector('.nt-feedback');
    if (fb) fb.remove();
  }

  function clearState(input) {
    input.style.borderColor = '';
    input.style.boxShadow   = '';
    const fb = input.parentElement.querySelector('.nt-feedback');
    if (fb) fb.remove();
  }

  function validateField(input) {
    const val = input.value.trim();

    // Campo obrigatório
    if (input.hasAttribute('required') || input.dataset.required === 'true') {
      if (!val) { showError(input, 'Campo obrigatório'); return false; }
    }

    // Valor numérico mínimo
    if (input.dataset.minVal !== undefined && val !== '') {
      const num = parseFloat(val.replace(',', '.'));
      const min = parseFloat(input.dataset.minVal);
      if (isNaN(num) || num < min) {
        showError(input, `Valor mínimo: ${min > 0 ? 'R$ ' + min.toFixed(2) : min}`);
        return false;
      }
    }

    // Formato telefone (opcional, mas se preenchido valida)
    if (input.dataset.phone === 'true' && val) {
      const digits = val.replace(/\D/g, '');
      if (digits.length < 10 || digits.length > 11) {
        showError(input, 'Digite um número válido (10 ou 11 dígitos)');
        return false;
      }
    }

    if (val || input.type === 'file') showOk(input);
    else clearState(input);
    return true;
  }

  function applyMask(input) {
    if (input.dataset.phone !== 'true') return;
    input.addEventListener('input', function () {
      let d = this.value.replace(/\D/g, '').slice(0, 11);
      if (d.length > 6) {
        d = d.length === 11
          ? `(${d.slice(0,2)}) ${d.slice(2,7)}-${d.slice(7)}`
          : `(${d.slice(0,2)}) ${d.slice(2,6)}-${d.slice(6)}`;
      } else if (d.length > 2) {
        d = `(${d.slice(0,2)}) ${d.slice(2)}`;
      } else if (d.length > 0) {
        d = `(${d}`;
      }
      this.value = d;
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form[data-nt-validate]').forEach(function (form) {
      const fields = form.querySelectorAll('input, select, textarea');

      fields.forEach(function (input) {
        applyMask(input);
        input.addEventListener('blur', function () { validateField(this); });
        input.addEventListener('input', function () {
          if (this.style.borderColor === COLOR_ERR) validateField(this);
        });
        input.addEventListener('change', function () { validateField(this); });
      });

      form.addEventListener('submit', function (e) {
        let ok = true;
        fields.forEach(function (input) {
          if (!validateField(input)) ok = false;
        });
        if (!ok) {
          e.preventDefault();
          // Scroll para o primeiro campo com erro
          const first = form.querySelector('[style*="rgba(239"]');
          if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
          // Shake no botão de submit
          const btn = form.querySelector('[type=submit]');
          if (btn) {
            btn.classList.add('nt-shake');
            setTimeout(() => btn.classList.remove('nt-shake'), 500);
          }
        }
      });
    });
  });
})();
