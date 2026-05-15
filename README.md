# Kommo Lead Analyzer

Ferramenta simples em Python para analisar mensagens/notas e leads do Kommo, encontrar leads sem resposta e gerar uma lista de prioridades para follow-up comercial.

## O que a ferramenta faz

- Lê leads e mensagens/notas de arquivos `JSON` ou `CSV` exportados.
- Opcionalmente busca dados da API do Kommo com `KOMMO_SUBDOMAIN` e `KOMMO_ACCESS_TOKEN`.
- Calcula sinais de compra, objeções, urgência, sentimento negativo e leads sem resposta.
- Gera relatório em `Markdown`, `JSON` ou `CSV`.
- Não usa dependências externas: basta Python 3.10+.

## Uso com arquivos exportados

```bash
python3 kommo_lead_analyzer.py \
  --leads-file examples/leads.json \
  --messages-file examples/messages.json \
  --output kommo_analysis.md
```

Para gerar CSV:

```bash
python3 kommo_lead_analyzer.py \
  --leads-file examples/leads.json \
  --messages-file examples/messages.json \
  --format csv \
  --output priorities.csv
```

## Uso com API do Kommo

A API v4 do Kommo usa URLs no formato `https://{subdomain}.kommo.com/api/v4/...`. A ferramenta consulta `leads` e `leads/notes` para montar a análise.

```bash
export KOMMO_SUBDOMAIN="sua-conta"
export KOMMO_ACCESS_TOKEN="seu-token-oauth"
python3 kommo_lead_analyzer.py --from-api --output kommo_analysis.md
```


## Rodar automaticamente com GitHub Actions

O repositório inclui o workflow `.github/workflows/kommo-analysis.yml` para rodar a análise pela API do Kommo manualmente ou todos os dias às 12:00 UTC.

Antes de executar, configure estes secrets no GitHub em **Settings → Secrets and variables → Actions**:

- `KOMMO_SUBDOMAIN`: apenas o subdomínio da conta, sem `https://` e sem `.kommo.com`.
- `KOMMO_ACCESS_TOKEN`: token de acesso novo/ativo da integração Kommo.

Depois, abra a aba **Actions**, selecione **Kommo Lead Analysis** e clique em **Run workflow**. Ao terminar, baixe o artifact `kommo-analysis-report`, que contém o arquivo `kommo_analysis.md`.

> Segurança: não coloque tokens no código, no README, em exemplos ou em commits. Se um token foi compartilhado em chat ou commit, revogue no Kommo e gere outro antes de salvar nos secrets do GitHub.

## Campos aceitos

A ferramenta tenta reconhecer nomes comuns de campos para facilitar o uso com exports diferentes:

- Leads: `id`, `lead_id`, `entity_id`, `name`, `status_id`, `pipeline_id`, `price`, `created_at`.
- Mensagens/notas: `lead_id`, `entity_id`, `parent_id`, `text`, `message`, `body`, `comment`, `note`, `created_at`, `direction`, `sender_type`, `note_type`.

Se a direção da mensagem não estiver clara, use valores como `incoming`/`outgoing`, `client`/`manager` ou prefixos no texto como `Cliente:` e `Vendedor:`.

## Exemplo de relatório

```markdown
# Análise de Leads e Mensagens Kommo

- Leads analisados: **2**
- Mensagens analisadas: **4**
- Leads sem resposta: **1**

## Top prioridades
| Score | Lead | Mensagens | Sem resposta | Recomendação |
```
