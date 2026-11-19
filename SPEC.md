# ESPECIFICAÇÃO ARQUITETURAL E DE DOMÍNIO: SISTEMA DE IDENTIFICAÇÃO E TARJAMENTO DE DADOS PESSOAIS — UFAM

## 1. VISÃO, OBJETIVO INSTITUCIONAL E FUNDAMENTAÇÃO JURÍDICA
Aplicação web institucional da Universidade Federal do Amazonas (UFAM), destinada às Pró-Reitorias e unidades administrativas, para identificação automatizada e **tarjamento definitivo (true redaction)** de dados pessoais e dados pessoais sensíveis em documentos PDF (textuais nativos, digitalizados/escaneados e híbridos).

### 1.1. Escopo Documental Institucional
A solução deve processar documentos administrativos da realidade da UFAM, incluindo: processos administrativos (SEI/SIPAC), despachos, ofícios, memorandos, relatórios, editais, portarias, declarações, documentos funcionais, documentos acadêmicos, documentos financeiros, documentos médicos e odontológicos (laudos, atestados, prontuários), formulários, planilhas convertidas em PDF e documentos digitalizados com ruído visual.

### 1.2. Diretrizes Jurídicas e Conceituais Obrigatórias (LGPD e LAI)
- **Denominação Oficial:** Sistema de Identificação e Tarjamento de Dados Pessoais — UFAM.
- **Distinção Conceitual Estrita:**
    - *Tarjamento / Redação Definitiva (Escopo do Sistema):* Remoção ou ocultação irreversível de informações específicas de um documento PDF, destruindo fluxos de texto, vetores e pixels subjacentes.
    - *Anonimização (Art. 5º, XI, Lei nº 13.709/2018 - LGPD):* Utilização de meios técnicos razoáveis e disponíveis no momento do tratamento por meio dos quais um dado perde a possibilidade de associação, direta ou indireta, a um indivíduo. **Regra de Sistema:** A interface e os relatórios devem alertar expressamente que o tarjamento de campos específicos remove os identificadores selecionados, mas **não garante a anonimização integral do documento** (devido ao risco de identificação indireta pelo contexto remanescente).
    - *Pseudonimização (Art. 13, § 4º, LGPD):* Tratamento pelo qual um dado perde a possibilidade de associação direta ou indireta, salvo pelo uso de informação adicional mantida separadamente pelo controlador em ambiente seguro.
- **Base Normativa:** Lei nº 13.709/2018 (LGPD — princípios da finalidade, adequação, necessidade, segurança, prevenção, responsabilização e prestação de contas), Lei nº 12.527/2011 (Lei de Acesso à Informação - LAI), Decreto nº 7.724/2012 e diretrizes da Autoridade Nacional de Proteção de Dados (ANPD).
- **Apoio à Decisão (Human-in-the-Loop):** A ferramenta é um instrumento técnico de apoio e **não substitui** a análise administrativa ou jurídica da unidade responsável, nem as orientações do Encarregado pelo Tratamento de Dados Pessoais (DPO/UFAM). Nenhum tarjamento deve ser aplicado sem revisão humana prévia.

---

## 2. TAXONOMIA DE DADOS E REGRAS DE CLASSIFICAÇÃO
O motor híbrido de detecção deve classificar as ocorrências nas 4 categorias estritas abaixo:

1. `PERSONAL_COMMON` (**Dado Pessoal Comum**):
    - Nome completo, CPF, RG, CNH, Passaporte, Título de Eleitor, PIS/PASEP/NIT;
    - Endereço residencial, CEP, endereço de e-mail, número de telefone (fixo e móvel);
    - Data de nascimento, naturalidade, filiação;
    - Matrícula SIAPE, matrícula acadêmica UFAM, número de inscrição em processos seletivos;
    - Assinaturas manuscritas ou visuais, fotografias de pessoas (3x4 ou faciais), identificadores pessoais em tabelas e formulários;
    - Números de processos administrativos (ex.: padrão SEI `23105.XXXXXX/XXXX-XX`) **exclusivamente quando associados no contexto imediato a uma pessoa natural identificada ou identificável**.
2. `PERSONAL_SENSITIVE` (**Dado Pessoal Sensível — Art. 5º, II, LGPD**):
    - Origem racial ou étnica (ex.: autodeclarações em bancas de heteroidentificação/cotas vinculadas a titular);
    - Convicção religiosa, opinião política;
    - Filiação a sindicato (ex.: rubricas ou fichas sindicais nominais) ou a organização de caráter religioso, filosófico ou político;
    - Dados referentes à saúde ou odontológicos (códigos CID-10/CID-11, atestados, laudos periciais, diagnósticos, prontuários);
    - Dados referentes à vida sexual, dados genéticos ou dados biométricos vinculados a uma pessoa natural.
    - *Regra Contextual:* Diferenciar a simples menção abstrata/normativa a uma característica de uma informação que efetivamente revele dado sensível de uma pessoa natural.
3. `FINANCIAL_BANKING` (**Dado Financeiro e Bancário**):
    - Número de conta bancária, agência bancária, Chave PIX, dados de cartões;
    - Informações de pagamento, remuneração individualizada, rendimentos, descontos em folha e transações financeiras.
    - *Regra Legal:* Tratar com alto rigor de segurança, mas **jamais classificar automaticamente como dado pessoal sensível**, respeitando a definição taxativa do Art. 5º, II da LGPD.
4. `MINOR_OR_DEPENDENT` (**Crianças, Adolescentes e Dependentes — Art. 14, LGPD**):
    - Dados de crianças e adolescentes, dados de dependentes funcionais, vínculos familiares e dados de identificação de terceiros.

### 2.1. Redução de Falsos Positivos e Agrupamento
- **Supressão Contextual:** Não classificar como dado pessoal: CNPJ institucional da UFAM, números de processos administrativos meramente organizacionais, números de portarias/editais/ofícios isolados, códigos de documentos ou datas de publicação/assinatura institucional.
- **Níveis de Confiança:** Cada ocorrência deve conter `confidence_score` (0.0 a 1.0) e classificação `HIGH` (>= 0.85), `MEDIUM` (0.60 a 0.84) ou `LOW` (< 0.60).
- **Agrupamento de Repetições (`group_id`):** Ocorrências idênticas em múltiplas páginas (ex.: mesmo CPF ou mesmo nome) devem receber um `group_id` determinístico em memória para permitir seleção/desmarcação em lote pelo servidor.

---

## 3. STACK TECNOLÓGICA E ARQUITETURA DE SOFTWARE
- **Padrão Arquitetural:** Monólito Modular no Backend com processamento assíncrono, seguindo Clean Architecture, princípios SOLID, Clean Code, Injeção de Dependências, tratamento centralizado de erros e configuração por variáveis de ambiente.
- **Backend:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.0 (Async), Alembic.
- **Fila Assíncrona, Cache e Streaming:** Redis 7 (Broker, Cache temporário com TTL estrito e Pub/Sub para eventos em tempo real) + Celery.
- **Processamento e Redação de PDF:**
    - `PyMuPDF` (`fitz`): Detecção de páginas textuais/escaneadas/híbridas, extração posicional de texto e tabelas, renderização de imagens `.webp` e aplicação de redação definitiva física (`add_redact_annot` + `apply_redactions`).
    - `pikepdf`: Inspeção contra scripts maliciosos, remoção de metadados (`/Info` e XML XMP), limpeza de objetos órfãos, remoção de camadas ocultas (`/OCProperties`), tratamento de anexos e eliminação de atualizações incrementais.
- **OCR Acelerado em CPU:** `rapidocr-onnxruntime` / `PaddleOCR` (executado via `onnxruntime`) como motor primário em português + `pytesseract` (`por`) como alternativa complementar + `Pillow`.
- **NLP e IA Local:** `spaCy` (`pt_core_news_lg`) + `EntityRuler` customizado para o domínio administrativo da UFAM + Regex pré-compilados + Validadores algorítmicos (dígitos verificadores de CPF, CNPJ, CNH, PIS/PASEP). **Proibido o envio de documentos para APIs externas de IA ou uso para treinamento.**
- **Frontend Thin-Client (Otimizado para PCs Antigos):** Next.js (App Router), TypeScript, Tailwind CSS, shadcn/ui, Lucide Icons. Visualização baseada em imagens `.webp` geradas pelo backend + camada vetorial `<svg>` interativa (sem uso de `PDF.js` ou `react-pdf` no cliente).
- **Banco de Dados:** PostgreSQL 16. Armazena exclusivamente usuários, configurações, controle de tarefas (`ProcessingJob`), estatísticas e histórico de auditoria (`AuditLog`), **sem jamais armazenar o texto dos dados pessoais encontrados**.
- **Autenticação:** Arquitetura baseada em OAuth2/OIDC (JWT) preparada para integração com Keycloak institucional e controle de acesso baseado em perfis (RBAC).
- **Infraestrutura:** Docker, Docker Compose, Nginx e volumes temporários isolados com rotina automática de expurgo.

---

## 4. TARJAMENTO DEFINITIVO, VALIDAÇÃO DE SEGURANÇA E RELATÓRIO
### 4.1. Mecanismo de Redação Definitiva (True Redaction)
É estritamente proibido ocultar informações desenhando apenas um retângulo preto sobre o PDF. O motor de redação deve obrigatoriamente:
1. Preservar o documento PDF original intacto e gerar um novo arquivo independente;
2. Remover fisicamente os glifos e fluxos de texto selecionados (`PDF_REDACT_TEXT_REMOVE`);
3. Queimar/remover definitivamente os pixels correspondentes quando o dado estiver em imagens ou páginas escaneadas (`PDF_REDACT_IMAGE_PIXELS`);
4. Remover ou reconstruir vetores tocados pela tarja (`PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED`);
5. Remover anotações, comentários e campos de formulário (`widgets`) nas regiões afetadas;
6. Limpar metadados desnecessários, camadas ocultas, versões incrementais e objetos não referenciados via `pikepdf`;
7. Preservar a formatação, layout, numeração de páginas, tabelas, imagens e textos não afetados.

### 4.2. Validação Automática Pós-Tarjamento
Após gerar o novo PDF, o sistema deve reabri-lo e validar automaticamente:
1. Que os dados selecionados não constam mais no texto extraído nem podem ser recuperados por copiar/colar ou pesquisa;
2. Que os pixels nas regiões tarjadas foram efetivamente destruídos/preenchidos;
3. Que não restaram camadas ocultas, objetos órfãos ou metadados preservando o conteúdo original;
4. Que o arquivo PDF final está íntegro e pode ser aberto normalmente.

### 4.3. Relatório de Processamento (Zero-PII)
Gerar relatório exportável em PDF via `ReportLab` contendo: cabeçalho institucional UFAM, nome do documento, hashes SHA-256 (original e final), total de páginas, data/hora, contadores por categoria (`PERSONAL_COMMON`, `PERSONAL_SENSITIVE`, `FINANCIAL_BANKING`, `MINOR_OR_DEPENDENT`), total selecionado, total tarjado, total mantido, páginas afetadas, resultado detalhado das verificações de segurança e **aviso legal de limitações** (diferença entre tarjamento e anonimização do Art. 5º, XI da LGPD). **Proibido reproduzir qualquer dado pessoal encontrado no relatório.**

---

## 5. REQUISITOS NÃO-FUNCIONAIS DE PERFORMANCE NO SERVIDOR (SLA INTERNO)
Para garantir fluidez operacional em documentos de **10 páginas** rodando em servidor CPU (sem GPU dedicada), o backend deve cumprir:
- **Entrega da Página 1 (First-Page-First):** Menos de **1,8 segundo** após o upload para liberar a tela de revisão ao usuário.
- **PDF 100% Textual (10 páginas):** Análise completa em menos de **3,0 segundos**; Tarjamento definitivo + Validação em menos de **2,0 segundos**.
- **PDF 100% Digitalizado/Escaneado (10 páginas em CPU):** Análise completa em segundo plano em menos de **12,0 segundos**.

### Regras Obrigatórias de Otimização de Backend:
1. **Model Pre-warming (Singleton Pattern):** É proibido carregar `spacy.load()` ou instanciar o motor de OCR dentro de rotas HTTP ou dentro do corpo de funções chamadas por página. Carregue os modelos uma única vez na inicialização do processo Worker (`@worker_process_init.connect`) e no `lifespan` do FastAPI, mantendo-os residentes em memória RAM.
2. **Aceleração ONNX Runtime em CPU:** Utilize `rapidocr-onnxruntime` (ou `PaddleOCR` com `use_onnx=True` e `enable_mkldnn=True`) para inferência vetorial rápida em CPU.
3. **Detecção Seletiva (Zero-Waste OCR):** Jamais execute OCR em páginas 100% textuais que não possuam imagens relevantes (maiores que 100x50px).
4. **Resolução Adaptativa de OCR:** Renderize páginas escaneadas para o OCR a **200 DPI** por padrão (`fitz.Matrix(200/72, 200/72)`), acionando fallback de 300 DPI com melhoria de contraste apenas se a confiança média da página for inferior a `0.70`.
5. **NLP Otimizado:** Desative componentes desnecessários do pipeline `spaCy` (`disable=["parser", "lemmatizer"]`) e utilize `nlp.pipe()` quando processar blocos textuais.
6. **Validação Pós-Tarjamento Cirúrgica por Recorte (`Clip Validation`):** Na validação pós-tarjamento, valide a extração textual global, mas execute a inspeção de pixels e re-OCR **exclusivamente sobre o recorte (`clip=redacted_rect`) das coordenadas tarjadas**, nunca re-escaneando páginas inteiras.

---

## 6. OTIMIZAÇÃO EXTREMA PARA CLIENTES LEGADOS (PCs DE 10 A 15 ANOS) E UX DE LATÊNCIA ZERO
As estações de trabalho das unidades administrativas da UFAM incluem computadores com 10 a 15 anos de uso (processadores dual-core antigos, 4 GB de RAM DDR3 e vídeo integrado sem aceleração moderna). Para impedir travamentos no navegador do servidor público:

1. **Arquitetura Thin-Client (Zero `PDF.js` no Navegador):**
    - **É proibido usar `pdfjs-dist` ou `react-pdf` no frontend.** A decodificação de PDF não deve ocorrer na CPU do cliente.
    - O Backend deve renderizar cada página do PDF como uma imagem estática `.webp` otimizada (150 DPI, `quality=80`, ~80 a 120 KB por página) via `PyMuPDF`.
    - O Frontend deve exibir a página usando uma tag `<img decoding="async" />` leve com um único elemento `<svg viewBox="0 0 1000 1000" preserveAspectRatio="none">` sobreposto para desenhar as tarjas.
2. **Streaming Progressivo Página a Página (Server-Sent Events - SSE):**
    - O worker deve processar a **Página 1 imediatamente**, salvar o `page_1.webp` e emitir o evento SSE `page_ready` da Página 1 (status `PARTIAL_REVIEW_READY`).
    - O Frontend deve abrir o Workspace de Revisão imediatamente com a Página 1 disponível para trabalho enquanto as páginas 2..N são processadas em segundo plano e adicionadas via SSE.
3. **DOM Virtualizado de Página Única (Single-Page Viewport):**
    - O visualizador deve montar no DOM **apenas a página ativa atual (`currentPage`)**, realizando `preload` invisível (`new Image()`) somente da página `currentPage + 1`. Nunca empilhe todas as páginas do PDF no DOM simultaneamente. O consumo de RAM da aba do navegador deve ficar abaixo de 80 MB.
4. **Desenho de Tarja Manual a 60 FPS (`useRef` Direct Manipulation):**
    - Na ferramenta de desenhar ou redimensionar tarja manual com o mouse, **é proibido chamar `useState` do React dentro do evento `onMouseMove`**.
    - Atualize os atributos `x`, `y`, `width` e `height` do elemento `<rect>` rascunho diretamente no DOM via `useRef` durante o movimento do mouse e persista no estado do React **apenas uma única vez no evento `onMouseUp`**.
5. **Interface Low-Spec Friendly (Sem Sobrecarga de GPU):**
    - É proibido utilizar filtros CSS pesados como `backdrop-blur`, sombras grandes (`shadow-2xl`, `drop-shadow`) ou animações infinitas de layout. Utilize cores sólidas de alto contraste, bordas nítidas e ícones estáticos leves.

---

## 7. GUARDRAILS RÍGIDOS DE IMPLEMENTAÇÃO E QUALIDADE PARA A IA
1. **Proibido Código Fictício (No Mocks / No Placeholders):** Nunca gere funções vazias, retornos simulados ou comentários como `# TODO: implementar lógica`. Entregue a implementação funcional completa em cada arquivo.
2. **Padronização Universal de Coordenadas:** Todas as Bounding Boxes trocadas entre Backend e Frontend devem usar coordenadas normalizadas `[x0_norm, y0_norm, x1_norm, y1_norm]` no intervalo `[0.0, 1.0]` relativas à largura e altura da página. No `<svg viewBox="0 0 1000 1000">`, basta multiplicar por `1000`; no `PyMuPDF`, basta multiplicar por `page.rect.width` e `page.rect.height`.
3. **Zero-PII em Logs e Banco de Dados:** O filtro de logging estruturado e as tabelas do PostgreSQL jamais devem registrar o valor textual de CPFs, nomes, contas bancárias ou dados de saúde extraídos dos PDFs.
4. **Testes Automatizados com Dados 100% Sintéticos:** Toda validação (`pytest` e `Playwright`) deve utilizar documentos e dados pessoais gerados sinteticamente, aferindo `Precision`, `Recall`, `F1-Score`, IoU de coordenadas, cumprimento dos SLAs de tempo e destruição irreversível dos dados tarjados.