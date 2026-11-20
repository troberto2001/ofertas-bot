# Tradução do produto: espanhol → português europeu

A tradução fica ligada por omissão e aplica-se ao título nas ofertas novas,
pré-visualizações de `/exemplo` e testes `/teste`, tanto em modo web como account.
Não altera o texto da fonte, preços, códigos, links ou o modelo da mensagem.

| Antes | Depois |
|---|---|
| Zapatillas Nike Air Max 90 | Sapatilhas Nike Air Max 90 |
| Camiseta Adidas Originals | T-shirt Adidas Originals |
| Bolso de mujer Michael Kors | Mala de senhora Michael Kors |
| Sudadera con capucha Nike Tech Fleece | Sweatshirt com capuz Nike Tech Fleece |
| Pantalones cortos Jordan Dri-FIT 23 | Calções Jordan Dri-FIT 23 |

No chat privado do bot:

- `/traducao`: consultar o estado.
- `/traducao ligar`: ativar (também aceita `on`).
- `/traducao desligar`: desativar (também aceita `off`).
- `/exemplo`: testar as últimas ofertas com a definição atual, sem publicar automaticamente.

As ofertas já preparadas para aprovação mantêm o título que foi mostrado.
A definição fica guardada depois de reiniciar o bot.

## Âmbito e limites

Usa um glossário local de nomes de produto e qualificadores; não precisa de
chaves, serviços externos ou novas dependências. Reconhece nomes no início
do título, incluindo depois de emojis/parênteses, e qualificadores como
«de mujer», «con capucha» ou «de manga corta» imediatamente a seguir.
No primeiro segmento desconhecido, para e mantém todo o resto literalmente.

É destinado ao formato `[produto em espanhol] [marca/modelo]`. Não é tradução
livre de frases: títulos que começam pela marca, palavras desconhecidas e
descrições posteriores à marca ficam intactos. Por exemplo, «Adidas Spezial -
colores especiales» não é alterado. Esta escolha evita modificar palavras
de modelos que também podem ser palavras espanholas. Nomes ambíguos que
coincidam com o glossário podem exigir desligar a tradução ou ajustar as regras.
O glossário pode ser ampliado em `app/title_translation.py`.

## Instalar por upload manual no GitHub

Extrai o ZIP e carrega as pastas `app` e `tests`, `.gitattributes`, `.dockerignore` e este ficheiro na raiz do
repositório. Mantém os caminhos: `app/main.py` substitui o existente e
`app/title_translation.py` é novo. Não carregues o ZIP como ficheiro no repositório.
Mantém os nomes dos ficheiros que começam por ponto: são dois ficheiros distintos.
O `.gitattributes` controla os finais de linha; o `.dockerignore` exclui segredos e
ficheiros locais do contexto de construção Docker. O pacote corrige a troca de
conteúdos encontrada na versão publicada desses ficheiros.

No servidor, se a instalação está gerida pelo `install.sh`:

```bash
cd ~/ofertas-bot && git pull && bash install.sh
```

Se manténs diretamente o código atualizado na pasta instalada, usa `botctl update`.
Depois, no Telegram, envia `/traducao` e `/exemplo`.
