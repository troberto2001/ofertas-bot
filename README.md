# Bot de ofertas (Telegram)

Lê ofertas de um canal, troca o código de desconto e o link pelo teu, copia a imagem e publica no teu canal. Corre num contentor Docker no teu VPS.

## Dois modos (`MODE` no `.env`)

| | **web** (por omissão) | **account** |
|---|---|---|
| Precisa de login de conta | **Não** | Sim (`botctl login`, com número de telemóvel) |
| Origem | Canais **públicos** (lê `t.me/s/canal`) | Grupos e canais, mesmo privados |
| Quem publica | O bot (tem de ser admin do teu canal) | A tua conta |
| Emojis premium | Não | Sim (se a conta tiver Premium) |
| Visível na origem | Invisível (não entra no canal) | Aparece como subscritor/membro |
| Atraso | ~2 minutos | Imediato |

## Instalar

```bash
git clone https://github.com/troberto2001/ofertas-bot.git && cd ofertas-bot && bash install.sh
```

O instalador instala o Docker (se faltar), pede as credenciais e arranca o bot. Precisas de:

1. **API_ID e API_HASH**: https://my.telegram.org → *API development tools*.
2. **BOT_TOKEN**: no Telegram, fala com o **@BotFather** → `/newbot`.
3. **OWNER_ID**: o teu ID numérico (fala com o **@userinfobot**). O bot só responde a ti.

## Configurar (modo web) — no Telegram

1. Adiciona o teu bot como **administrador do teu canal** (com permissão de publicar).
2. Abre o bot, `/start`.
3. `/adicionar t.me/nomedocanal` — canal público de origem.
4. `/destino @teucanal` — o teu canal (privado: `/destino` e reencaminha-lhe uma mensagem do canal).
5. `/linkmodelo LINK` — um link de um produto gerado na tua conta de afiliado.
6. `/codigo MEUCODIGO` — o teu código.
7. Começa em **manual** (aprovas cada oferta). Quando confiares: `/auto`.

Só publica ofertas **novas** a partir do momento em que adicionas o canal.

### Forçar / ver exemplos (as 5 últimas)

No chat do bot, o botão **🔄 Forçar: mostrar as 5 últimas** (aparece no `/start` e no `/estado`) ou o comando `/exemplo` (`/exemplo 10` para mais, máximo 10) vai buscar as ofertas mais recentes da origem, **mesmo as que já tinham passado** (ignora o "só novas" e o anti-duplicados), converte os links e envia cada uma ao teu chat com **Publicar / Rejeitar**. Nada é publicado sozinho, mesmo em `/auto`. As já publicadas nas últimas 24h vêm marcadas. No fim recebes um resumo, com o motivo das que não foi possível converter.

**Teste sem link:** o botão **🧪 Forçar sem link (teste)** ou `/teste` faz o mesmo, mas **não converte o link** (a linha do link é omitida). Serve para validar o formato, a imagem e as permissões do canal de destino sem teres o `/linkmodelo` feito. Se carregares em *Publicar*, a oferta vai para o canal tal como está; estes testes não ficam no histórico (`/ultimas`) nem contam para o limite por hora.

Outros comandos: `/estado` `/ultimas` `/pausar` `/retomar` `/limite N` `/fontes` `/remover` `/modelo` `/recarregar`.

### Como converte o link

1. Segue o link da mensagem (incluindo encurtadores como onlyaff.app) até chegar à loja.
2. Extrai o ID do produto.
3. Monta o **teu** link a partir do modelo que guardaste com `/linkmodelo` (só muda o ID do produto).

Se não conseguir converter (loja desconhecida, formato mudou, falta o `/linkmodelo`), **não publica nada** e avisa-te. Nunca republica o link de outro afiliado.

Lojas: vêm definidas em `data/rules.json` (a Hacoo vem de origem). Para outra loja, acrescenta uma regra com `host_re`, `id_re` (o grupo 1 é o ID) e `template`, e envia `/recarregar`.

## Modo account

Define `MODE=account` no `.env`, corre `botctl login` e, no pedido, escreve o **número de telemóvel** (nunca o token do BotFather: um bot não lê grupos). Depois usa `/escolher` e `/destino` (botões). Emojis premium: `/modelo` e envia a mensagem com os emojis.

## Gestão no servidor

```
botctl status | logs | restart | update | backup | login | uninstall [--purge]
```

Atualizar o código: `cd ~/ofertas-bot && git pull && bash install.sh` (mantém o `.env` e os dados).

## Segurança

- O painel só responde ao dono (`OWNER_ID`). O contentor não expõe portas e corre sem privilégios.
- `data/` contém a base de dados e, no modo account, a sessão da conta (`user.session`), que dá acesso total à conta: não a partilhes. `botctl backup` inclui-a.
- Modo account: usar a conta pessoal como userbot não é oficialmente suportado pelo Telegram. Risco baixo com o limite de publicações por hora (`/limite`); considera uma conta separada.

## Cuidados legais

Confirma que as regras do canal de origem e do programa de afiliados permitem republicar o conteúdo, e que os artigos promovidos são legítimos (marcas a preços muito baixos podem ser contrafações).

## Limitações

- Modo web: só canais públicos com pré-visualização web; só a primeira foto de cada mensagem; sem emojis premium.
- Redirecionamentos feitos só por JavaScript complexo não são seguidos (HTTP, `meta refresh` e `location=` simples sim).
- Canais com "restringir guardar conteúdo" podem impedir a cópia da imagem no modo account.
