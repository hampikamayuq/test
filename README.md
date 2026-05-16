# Kommo Lead Analyzer — Clínica QARA

Ferramenta em Python para analisar leads e mensagens do Kommo e gerar relatórios de prioridade de follow-up comercial, com detecção automática de especialidade, canal de captação, cidade e etapa do funil do bot.

Não usa dependências externas: basta Python 3.10+.

## O que a ferramenta faz

- Lê leads e mensagens/notas de arquivos `JSON` ou `CSV` exportados, ou busca direto pela API do Kommo.
- Detecta automaticamente:
  - **Canal de captação** — Instagram, anúncio pago, site/página, indicação direta.
  - **Especialidade solicitada** — unhas (Dr. Miguel), cabelo (Dra. Diana), cirurgia (Dr. Diego), psoríase/dermatite/hidradenite (Dra. Manuela), dermatopediatria, estética, pele geral.
  - **Cidade preferida** — Copacabana, Barra da Tijuca, São Paulo.
  - **Etapa do funil do bot** — desde "sem interação" até "chegou ao agendamento" ou "fallback para equipe".
- Calcula sinais de compra, objeções, urgência, sentimento negativo e tempo sem resposta.
- Ordena leads por urgência: vencidos sem resposta → sem resposta → score de prioridade.
- Gera relatório completo em `Markdown`, `HTML` interativo (gráficos Chart.js), `JSON` ou `CSV`.

## Relatório gerado

O relatório inclui (Markdown e HTML):

1. **Visão Geral** — totais, leads sem resposta, tempo médio de resposta e primeira resposta.
2. **Canal de Captação** — distribuição por origem com percentual.
3. **Por Especialidade** — leads, quantos agendaram e taxa de conversão por especialidade.
4. **Por Unidade** — distribuição por cidade.
5. **Funil do Bot** — quantos leads chegaram a cada etapa.
6. **Tipo de Pagamento** — convênio vs. particular (quando mencionado).
7. **Distribuição Temporal** — leads por hora (UTC) e por dia da semana.
8. **⚠️ Urgente** — leads vencidos sem resposta com ID, especialidade, cidade e horas de espera.
9. **🔔 Tarefa Vencida** — leads com tarefa do Kommo atrasada.
10. **📬 Aguardando Resposta** — leads sem resposta ainda dentro do prazo.
11. **Top Prioridades** — tabela completa com ID, canal, especialidade, cidade, etapa do bot, score e recomendação.

O **HTML** usa **Chart.js** com gráficos interativos de rosca (canal, cidade), barras horizontais (especialidade, funil) e barras verticais (hora, dia). O **ID do lead** aparece em destaque em todas as tabelas para facilitar a busca direta no Kommo.

## Uso com arquivos exportados

```bash
python3 kommo_lead_analyzer.py \
  --leads-file examples/leads.json \
  --messages-file examples/messages.json \
  --output kommo_analysis.md
```

Gerar HTML interativo (Chart.js):

```bash
python3 kommo_lead_analyzer.py \
  --leads-file examples/leads.json \
  --messages-file examples/messages.json \
  --format html \
  --output relatorio.html
```

Gerar CSV:

```bash
python3 kommo_lead_analyzer.py \
  --leads-file examples/leads.json \
  --messages-file examples/messages.json \
  --format csv \
  --output priorities.csv
```

## Uso com API do Kommo

```bash
export KOMMO_SUBDOMAIN="sua-conta"
export KOMMO_ACCESS_TOKEN="seu-token-oauth"

# Opcional: necessário para ler o histórico completo dos Talks via Chats API.
export KOMMO_CHAT_SCOPE_ID="scope-id-do-canal"
export KOMMO_CHAT_SECRET="secret-do-canal"

python3 kommo_lead_analyzer.py \
  --from-api \
  --stale-hours 48 \
  --top-n 30 \
  --output kommo_analysis.md
```

O `KOMMO_ACCESS_TOKEN` permite buscar leads, notas e a lista de Talks pela API v4. Para ler o histórico de mensagens de cada Talk, a Kommo usa a **Chats API** (`amojo.kommo.com`), que exige assinatura com `scope_id` e `secret` do canal. Se `KOMMO_CHAT_SCOPE_ID` e `KOMMO_CHAT_SECRET` não estiverem configurados, a ferramenta pula as mensagens de Talks e mostra um aviso no log.

Gerar HTML a partir da API:

```bash
python3 kommo_lead_analyzer.py \
  --from-api \
  --format html \
  --output relatorio.html
```

Re-renderizar sem chamar a API novamente (útil para mudar o formato):

```bash
# Gera o JSON uma vez
python3 kommo_lead_analyzer.py --from-api --format json --output analise.json

# Re-renderiza em HTML e Markdown sem nova chamada à API
python3 kommo_lead_analyzer.py --from-report analise.json --format html --output relatorio.html
python3 kommo_lead_analyzer.py --from-report analise.json --format markdown --output relatorio.md
```

Filtrar por período:

```bash
python3 kommo_lead_analyzer.py \
  --from-api \
  --filter-from 2026-05-01 \
  --filter-to 2026-05-31 \
  --output maio.md
```

## Argumentos disponíveis

| Argumento | Padrão | Descrição |
| --- | --- | --- |
| `--leads-file` | — | Arquivo JSON/CSV com leads exportados. Mutuamente exclusivo com `--from-api`. |
| `--from-api` | — | Busca leads e notas pela API do Kommo. |
| `--from-report` | — | Lê um relatório JSON gerado anteriormente e re-renderiza sem chamar a API. |
| `--messages-file` | — | Arquivo JSON/CSV com mensagens/notas. Obrigatório com `--leads-file`. |
| `--output` | `kommo_analysis.md` | Caminho do arquivo de saída. |
| `--format` | `markdown` | Formato: `markdown`, `html`, `json` ou `csv`. |
| `--stale-hours` | `48` | Horas sem resposta para considerar o lead "vencido". |
| `--top-n` | `30` | Número de leads exibidos nas tabelas do relatório. |
| `--filter-from` | — | Data ISO 8601 de início do filtro (só com `--from-api`). Ex: `2026-01-01`. |
| `--filter-to` | — | Data ISO 8601 de fim do filtro (só com `--from-api`). Ex: `2026-12-31`. |
| `--no-delta` | — | Desativa o relatório delta (não lê nem salva snapshot anterior). |
| `--probe` | — | Modo diagnóstico: inspeciona endpoints da API para 1 lead (requer `--from-api`). |

## Rodar automaticamente com GitHub Actions

O workflow `.github/workflows/kommo-analysis.yml` executa a análise manualmente ou todos os dias às 12:00 UTC.

Configure estes secrets em **Settings → Secrets and variables → Actions**:

| Secret | Obrigatório | Descrição |
| --- | --- | --- |
| `KOMMO_SUBDOMAIN` | ✅ | Subdomínio da conta, sem `https://` e sem `.kommo.com`. |
| `KOMMO_ACCESS_TOKEN` | ✅ | Token de acesso ativo da integração Kommo. |
| `KOMMO_CHAT_SCOPE_ID` | — | Scope ID do canal da Chats API para ler histórico de Talks via WhatsApp. |
| `KOMMO_CHAT_SECRET` | — | Secret do canal da Chats API (par com `KOMMO_CHAT_SCOPE_ID`). |
| `SLACK_WEBHOOK_URL` | — | URL do Incoming Webhook do Slack para notificação automática ao final. |

Após configurar, abra a aba **Actions**, selecione **Kommo Lead Analysis** e clique em **Run workflow**.

O workflow:
1. Chama a API do Kommo **uma única vez** e salva o JSON.
2. Re-renderiza o Markdown e o HTML a partir do JSON (sem nova chamada à API).
3. Envia notificação no Slack (se `SLACK_WEBHOOK_URL` estiver configurado).
4. Disponibiliza **dois artifacts**: `kommo-analysis-markdown` e `kommo-analysis-html`.

> **Segurança:** nunca coloque tokens no código, no README, em exemplos ou em commits. Se um token foi compartilhado em chat ou commit, revogue no Kommo e gere outro antes de salvar nos secrets do GitHub.

## Campos aceitos

A ferramenta reconhece nomes comuns de campos para compatibilidade com diferentes exports:

- **Leads:** `id`, `lead_id`, `entity_id`, `name`, `status_id`, `pipeline_id`, `price`, `created_at`.
- **Mensagens/notas:** `lead_id`, `entity_id`, `parent_id`, `text`, `message`, `body`, `comment`, `note`, `created_at`, `direction`, `sender_type`, `note_type`.

A direção da mensagem é detectada por: campo `direction`/`sender_type`, `note_type` inteiro do Kommo (4=chamada entrada, 10=chamada saída, 25=SMS entrada, 26=SMS saída, 102/103=mensagens), ou prefixo no texto (`Cliente:`, `Vendedor:`).

## Exemplo de relatório

```markdown
# Análise de Leads — Clínica QARA
_Gerado em 2026-05-15T12:00:00Z_

## Canal de Captação
| Canal            | Leads | %   |
| Instagram        | 43    | 30% |
| Anúncio          | 38    | 27% |
| Site/Página      | 35    | 24% |
| Direto/Indicação | 27    | 19% |

## Por Especialidade
| Especialidade          | Leads | Agendou | Conv. % |
| Unhas (Dr. Miguel)     | 52    | 31      | 60%     |
| Cabelo (Dra. Diana)    | 28    | 18      | 64%     |
| Cirurgia (Dr. Diego)   | 27    | 18      | 67%     |

## ⚠️ Urgente — Sem Resposta Vencida
| ID       | Lead        | Especialidade      | Cidade     | Sem resp. |
| `123456` | Maria Silva | Unhas (Dr. Miguel) | Copacabana | 72.3h     |

## Top Prioridades
| ID       | Lead     | Canal     | Especialidade      | Cidade     | Etapa Bot              | Msgs | Sem resp. | Score | Recomendação |
| `123456` | Maria... | Instagram | Unhas (Dr. Miguel) | Copacabana | Chegou ao agendamento  | 5    | 72.3h ⚠️  | 85    | Responder agora... |
```
