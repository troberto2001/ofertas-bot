# Bot de ofertas (Telegram)

Lê ofertas de um grupo, troca o código de desconto e o link pelo teu, copia a imagem e publica no teu canal. Corre num contentor Docker no teu VPS.

## Instalar (um comando)

```bash
unzip ofertas-bot.zip && cd ofertas-bot && bash install.sh
```

O instalador instala o Docker (se faltar), pede as credenciais, faz o login do Telegram e arranca o bot. Precisas de ter à mão:

1. **API_ID e API_HASH**: https://my.telegram.org → *API development tools*.
2. **BOT_TOKEN**: no Telegram, fala com o **@BotFather** → `/newbot`. É o teu painel de controlo.
3. A tua conta de Telegram (o login pede o código que recebes no telemóvel).

## Configurar (no Telegram, no teu bot)

| Passo | Comando |
|---|---|
| Abrir o painel | `/start` |
| Grupos de origem | `/escolher` (botões) ou `/adicionar LINK` |
| Canal de destino | `/destino` (a tua conta tem de ser admin do canal) |
| O teu link de afiliado | `/linkmodelo LINK` com um link de um produto gerado na tua conta |
| O teu código | `/codigo MEUCODIGO` |
| Texto e emojis | `/modelo` (aceita emojis premium) |
| Publicar sozinho | `/auto` (começa em `/manual`: aprovas cada oferta) |

Outros: `/estado` `/ultimas` `/pausar` `/retomar` `/limite N` `/fontes` `/remover` `/topico` `/recarregar`.

### Como converte o link

1. Segue o link da mensagem (incluindo encurtadores como onlyaff.app) até chegar à loja.
2. Extrai o ID do produto.
3. Monta o **teu** link a partir do modelo que guardaste com `/linkmodelo` (só muda o ID do produto).

Se não conseguir converter (loja desconhecida, formato mudou, falta o `/linkmodelo`), **não publica nada** e avisa-te. Nunca republica o link de outro afiliado.

Lojas: vêm definidas em `data/rules.json` (a Hacoo vem de origem). Para outra loja, acrescenta uma regra com `host_re`, `id_re` (o grupo 1 é o ID) e `template`, e envia `/recarregar`.

## Emojis premium

O modelo guarda os emojis premium que lhe enviares. Só são **enviados** se a conta com que fizeste login tiver Telegram Premium; quem lê vê-os sem Premium. Sem Premium, saem como o emoji normal equivalente.

## Gestão no servidor

```
botctl status | logs | restart | update | backup | login | uninstall [--purge]
```

## Segurança

- `data/user.session` dá acesso total à tua conta de Telegram. Está com permissões restritas; não a partilhes nem a envies a ninguém. `botctl backup` inclui-a.
- O painel só responde ao dono (a conta do login, ou `OWNER_ID`).
- O contentor não expõe portas e corre sem privilégios.
- Usar a conta pessoal como userbot não é oficialmente suportado pelo Telegram. O risco é baixo com o limite de publicações por hora (`/limite`) e só leitura nos grupos; considera uma conta separada.

## Cuidados legais

Confirma que as regras do grupo de origem e do programa de afiliados permitem republicar o conteúdo, e que os artigos promovidos são legítimos (marcas a preços muito baixos podem ser contrafações).

## Limitações conhecidas

- Os redirecionamentos feitos só por JavaScript complexo não são seguidos (os HTTP, `meta refresh` e `location=` simples sim).
- Só copia a primeira foto de cada mensagem (não álbuns completos).
- Formatação (negrito, etc.) do modelo não é guardada, só emojis premium.
