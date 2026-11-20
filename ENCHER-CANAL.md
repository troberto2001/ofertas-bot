# Encher o canal com 50 ofertas

No chat privado do bot, como dono:

- `/encher`: inicia um lote de 50 publicações no destino configurado.
- `/encher_estado`: mostra progresso, fase, falhas e limite por hora.
- `/encher_parar`: interrompe esse lote depois da operação em curso.
- `/pausar`: também interrompe o lote e pausa a leitura/publicação normal.

Cada `/encher` é uma ordem para **publicar diretamente**, mesmo em modo manual,
sem pedir aprovação de cada oferta. Não altera o modo manual/automático normal.
Só pode existir um enchimento ativo. Quando termina ou é interrompido, podes
enviar `/encher` outra vez, sem um limite de execuções.

## Seleção e publicação

Recolhe até 300 mensagens por fonte ativa. No modo web, percorre até 15 páginas
de histórico por fonte; no modo account, respeita também o tópico configurado.
Exclui o próprio canal de destino. Usa os mesmos critérios de oferta/imagem do
bot. Não altera o marcador de leitura das fontes nem os pendentes de aprovação.

Mistura as ofertas de cada fonte e publica uma candidata de cada fonte por
ronda, em ordem aleatória. Evita repetir a fonte entre rondas quando há outras
disponíveis. Fontes esgotadas saem da rotação; com uma única fonte, usa essa.
Falhas e produtos duplicados podem alterar a alternância das publicações efetivas.

Dentro do lote, não repete o mesmo produto. Entre execuções pode reutilizar
produtos já publicados, mesmo dentro do período normal de deduplicação. Não é
garantido que um novo lote contenha 50 produtos diferentes do lote anterior.

Converte os links pelo método configurado (gerador Hacoo ou link modelo), traduz
o título com o glossário ES → PT existente e aplica o código de desconto/modelo
da mensagem. Neste comando, a tradução é sempre aplicada, mesmo se `/traducao`
estiver desligada para as publicações normais. Frases fora do glossário mantêm-se.

Conta apenas os envios concluídos: falhas não contam para as 50 publicações.
Se faltarem ofertas válidas/distintas dentro do histórico recolhido, termina
com menos de 50 e avisa do total. O histórico e os limites registam estes envios.
Alterar o destino ou pausar interrompe o lote. Remover uma fonte impede usar as
suas candidatas restantes. A paragem não desfaz mensagens já enviadas nem um
pedido de envio que o Telegram já esteja a processar.

## Duração e repetição

Mantém `/limite`, `MIN_INTERVAL_SEC` e as esperas exigidas pelo Telegram.
Com os valores habituais de 12/hora e intervalo de 20 segundos, 50 publicações
podem demorar mais de quatro horas. Se quiseres permitir até 50 por hora,
configura `/limite 50` antes de começar; os restantes limites continuam ativos.
As publicações normais também contam para esse limite e podem intercalar-se.

O trabalho corre em segundo plano: os comandos de controlo continuam disponíveis.
`/retomar` retoma o funcionamento normal, mas não reinicia um lote interrompido;
para isso, envia `/encher`. Reiniciar o processo termina o lote em curso, sem
retoma automática. Os envios concluídos ficam no histórico SQLite.

## Upload manual e instalação

Extrai `encher-canal-github.zip`. Carrega **todos os ficheiros extraídos na raiz
do repositório GitHub**, substituindo os ficheiros com os mesmos nomes. Neste
pacote, o instalador coloca os quatro módulos Python na pasta `app/` por ti.
Não carregues o ZIP fechado e não renomeies os ficheiros.

Depois, no terminal do servidor:

```bash
cd ~/ofertas-bot && git pull --ff-only && bash instalar-enchimento.sh
```

No fim deve aparecer «Enchimento e traducao instalados». Só depois usa `/encher`
no Telegram. O instalador não pede nem mostra credenciais; mantém as definições
e dados existentes através do instalador normal do projeto.

## Validação

Testes simulam lotes de 50 nos dois modos, repetição de lotes, seleção aleatória
equilibrada, paginação, proteção de comandos, tradução, conversão, duplicados,
fontes removidas, destino alterado, falhas, limites e paragem durante esperas.
Não foram enviadas publicações reais nem executada uma instalação Docker real.
