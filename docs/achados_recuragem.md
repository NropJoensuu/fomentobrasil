# Achados da recuragem dos 15 registros de referência

O briefing que motivou as correções desta seção (2026-09-16/17) referenciava "a tabela do
usuário" com os achados completos da recuragem manual dos 15 registros de referência do
gabarito da IA, a ser colada aqui. A tabela em si não chegou ao agente — só os itens
específicos descritos em texto no corpo do briefing, que são os documentados abaixo. Se a
tabela completa existir em outro lugar (planilha, documento externo), vale colar aqui para
registro histórico; o que segue é reconstituído a partir do que foi corrigido e validado.

Ver `docs/inventario_de_necessidades.md`, seção "Correções da recuragem dos 15 registros
(2026-09-17)" para o relato completo de causa/correção/validação de cada item.

## Achados usados para orientar correções de código

| # registro | Achado relatado | Campo afetado | Resultado |
|---|---|---|---|
| #21 | Valor `7552000` exibido como R$ 75.520,00 (÷100) | orcamento_total_chamada | Corrigido (1.1) |
| #291 | Valor `20000` exibido como R$ 200,00 (÷100) | valor_minimo/maximo_proposta | Corrigido (1.1) |
| #23 | Acento escapado na tela (`eventos cientídicos`) | palavras_chave (exibição) | Corrigido (1.2) |
| #23 | `abrangencia` marcada `nacional` quando o edital admite nacional OU internacional | abrangencia | Corrigido (3.2), confirmado ao vivo |
| #23 | `proponente_elegivel` incluiu `pesquisadores` além de `ict`, mas a evidência citada fala só em "instituições proponentes" | proponente_elegivel | Corrigido (3.1), confirmado ao vivo |
| #81 | Regra pegou 29/04/2020 (data de um Decreto citado na assinatura) como `data_publicacao`, em vez de 15/07/2026 | data_publicacao (regra) | Corrigido (1.3), confirmado contra o PDF real |
| #82 | PDF "atualizado" mas leitura (regra e IA) trouxe prazo antigo (24/08 em vez de 31/08) | data_prazo | Causa identificada (1.4): página fonte tinha 3 PDFs (original + retificação + retificado), sugestão da IA lida contra o original. Aviso de sugestão desatualizada implementado |
| #93 | IA não retornou `palavras_chave` | palavras_chave | Investigado (1.5): não é bug de palavras-chave — só 7 de ~18 campos vieram, porque o PDF tem 98 páginas e só as 8 primeiras são lidas (MAX_PAGINAS_PDF). Não alterado (troca de cobertura por custo, decisão do usuário) |
| #34 | `uf` marcada com as 9 UFs da Amazônia Legal deduzindo do nome do programa | uf | Instrução adicionada (3.3); no teste ao vivo o PDF real cita as 9 UFs explicitamente no texto, então o comportamento correto aqui é marcá-las — mas a regra passou a exigir citação textual, não dedução |
| #83 | `linha_de_fomento` marcada `auxilio_inovacao` por menção temática a "inovação", quando o objeto é infraestrutura laboratorial | linha_de_fomento | Corrigido (3.4), confirmado ao vivo: passou a `auxilio_pesquisa` |
| #300 | Dúvida do usuário sobre o que "Rejeitar" faz; IA sugeriu corretamente `e_fomento=false` | status / e_fomento | Motivou a redefinição de `status` (2.4) e o tooltip do botão Rejeitar (4.1) |
| #376 | Programas guarda-chuva nacionais (Centelha, Tecnova, PROFIX...) sem campo próprio | programa | Campo novo (2.1) |
| #377 | Bolsas para professores da educação básica sem `nivel_formacao` adequado | nivel_formacao | `educacao_basica` adicionado (2.3) |
| #291 (FAPESQ/CONFAP/Bélgica) | Chamada multilateral com ato de promover distribuído entre instituições | instituicao_promotora | Virou ARRAY (2.2) |
| #160, #215, #377 | proponente com pessoas acrescentadas quando evidência descreve só a instituição | proponente_elegivel | Coberto pela mesma instrução de 3.1 (não validado ao vivo nestes três especificamente — só #23) |
