"""
Serviço de Inteligência Artificial — Nascimento Tech
Usa Claude (Anthropic) para:
  - Analisar comprovantes de pagamento (Vision)
  - Assistente de chat para atendentes
"""
import os
import base64
import json
import re

_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            from anthropic import Anthropic
            api_key = os.environ.get('ANTHROPIC_API_KEY', '')
            if not api_key:
                return None
            _client = Anthropic(api_key=api_key)
        except Exception:
            return None
    return _client


def ai_available() -> bool:
    """Retorna True se a IA está configurada e disponível."""
    return bool(os.environ.get('ANTHROPIC_API_KEY', ''))


# ── Análise de Comprovante ─────────────────────────────────────────────────────

def analyze_comprovante(image_bytes: bytes, ext: str, expected_amount: float) -> dict:
    """
    Analisa um comprovante de pagamento usando Claude Vision.

    Retorna dict:
      time         — "HH:MM" extraído da imagem, ou None
      amount       — valor numérico extraído, ou None
      amount_match — True se valor confere com expected_amount
      suspicious   — True se há sinais de adulteração/fraude
      notes        — observação resumida em português
    """
    client = _get_client()
    if not client:
        return {}

    # Apenas imagens suportadas por Vision
    media_map = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
        'png': 'image/png',  'webp': 'image/webp',
        'gif': 'image/gif',
    }
    media_type = media_map.get(ext.lower())
    if not media_type:
        return {}  # PDF e outros não suportados por Vision

    b64 = base64.standard_b64encode(image_bytes).decode('utf-8')

    prompt = (
        f"Analise este comprovante de pagamento.\n"
        f"Valor esperado pelo sistema: R$ {expected_amount:.2f}\n\n"
        "Responda SOMENTE em JSON válido, sem texto adicional:\n"
        "{\n"
        '  "time": "HH:MM ou null",\n'
        '  "amount": valor_numerico_ou_null,\n'
        '  "amount_match": true_ou_false,\n'
        '  "suspicious": true_ou_false,\n'
        '  "notes": "observacao_breve_em_portugues"\n'
        "}\n\n"
        "Regras:\n"
        '- "time": hora:minuto do pagamento visível. null se não encontrar.\n'
        '- "amount": valor total pago. null se não encontrar.\n'
        f'- "amount_match": true se valor confere com R$ {expected_amount:.2f} '
        "(aceite diferença de até R$ 0,50).\n"
        '- "suspicious": true se imagem parece editada, print de print, '
        "resolução suspeita, metadados inconsistentes ou qualquer sinal de fraude.\n"
        '- "notes": frase curta explicando o resultado.'
    )

    try:
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            messages=[{
                'role': 'user',
                'content': [
                    {
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': b64,
                        }
                    },
                    {'type': 'text', 'text': prompt}
                ]
            }]
        )
        text = response.content[0].text.strip()
        # Remove bloco markdown se presente
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception:
        return {}


# ── Assistente de Chat ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """Você é o assistente virtual do sistema VendasPRO da Nascimento Tech.
Ajude os atendentes com dúvidas sobre o sistema, regras de comissão, clientes e vendas.

REGRAS DO SISTEMA:
- Horário normal: 08h às 22h (horário de Brasília)
- Fora desse horário = hora extra (requer aprovação do admin)
- Comissão normal: progressiva de 5% a 10% conforme meta mensal
- Comissão hora extra: 20% fixo
- Comprovante de pagamento é obrigatório em toda venda
- Clientes têm classificação: Painel (Gol/Star) e Suporte (Suporte 2/Theus/Impostor)

VOCÊ PODE AJUDAR COM:
- Como registrar uma venda, renovação ou novo cliente
- Dúvidas sobre comissão e hora extra
- Como usar as funcionalidades do sistema
- Dicas de atendimento ao cliente
- Pesquisas gerais sobre vendas e atendimento

VOCÊ NÃO DEVE:
- Revelar senhas, dados financeiros de outros atendentes ou informações sigilosas
- Modificar dados do sistema (apenas oriente o usuário)
- Responder perguntas completamente fora do contexto de trabalho

Seja objetivo, amigável e fale sempre em português brasileiro."""


def chat_with_ai(messages: list, attendant_name: str = '') -> str:
    """
    Envia mensagens para o assistente IA e retorna a resposta.
    messages: lista de {'role': 'user'|'assistant', 'content': '...'}
    """
    client = _get_client()
    if not client:
        return (
            'A IA não está configurada ainda. '
            'Solicite ao administrador que adicione a chave ANTHROPIC_API_KEY nas configurações.'
        )

    system = _SYSTEM_PROMPT
    if attendant_name:
        system += f'\n\nAtendente logado: {attendant_name}'

    try:
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=1024,
            system=system,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        return f'Erro ao consultar IA: {str(e)}'
