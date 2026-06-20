# Visual Automation

Framework local em Python para automacao visual baseada em screenshots, template matching, regioes de tela, state machine, logs e parada de emergencia.

Inspirado pela arquitetura de automacao visual aplicada a games, mas desenhado para rodar offline/sandbox no macOS com MSS, OpenCV, NumPy e PyAutoGUI/pynput.

## Requisitos

- macOS em Apple Silicon
- Python 3.11 ou superior
- Permissoes do macOS para o app de terminal usado:
  - **Accessibility**: permite mover/clicar o mouse e ouvir hotkeys.
  - **Screen Recording**: permite capturar a tela com MSS.

Em macOS, abra **System Settings > Privacy & Security** e habilite essas permissoes para Terminal, iTerm, VS Code ou o app que executar o Python.

## Instalacao

```bash
cd "/Users/carlosnossa/Desktop/New Project-Python/visual_automation"
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Crie seus arquivos locais de configuracao:

```bash
cp config/settings.example.json config/settings.json
cp config/regions.example.json config/regions.json
```

Se `python3.12` nao existir, use `python3.11` ou `python3.13`. Evite Python experimental/pre-release para automacao desktop, porque dependencias nativas podem variar mais.

## Templates

Adicione imagens pequenas em `assets/templates/`, por exemplo `button.png`.

Boas praticas:

- Recorte exatamente o elemento que voce quer encontrar.
- Evite templates grandes demais.
- Prefira PNG.
- Atualize o template se a UI mudar de escala, tema, idioma ou resolucao.

## Regioes

Configure `config/regions.json` no formato:

```json
{
  "main_area": {"left": 0, "top": 0, "width": 800, "height": 600}
}
```

Usar regioes melhora performance e reduz falso positivo.

## Rodar Demo

```bash
cd visual_automation
source .venv/bin/activate
python scripts/demo_loop.py
```

O demo procura o primeiro template em `assets/templates/`, clica quando encontrar e repete ate uma condicao de parada.
Por seguranca, `dry_run` vem como `true`: ele registra onde clicaria, mas nao clica. Para clicar de verdade, altere `config/settings.json`:

```json
{
  "dry_run": false
}
```

Se nao houver template, ele registra um aviso e sai sem clicar em nada.

Para apontar uma coordenada na tela sem clicar:

```bash
python scripts/point_at.py 460 410
```

## Gravar e Repetir Cliques Dentro da VM

Para nao sequestrar o mouse do macOS principal, rode este script no Terminal
dentro da VM do UTM (`VM macOS`). Se voce rodar no host, o host vai receber os
movimentos do mouse.

Dentro da VM, instale o projeto e dependencias como de costume. O Terminal da VM
precisa de permissao em **System Settings > Privacy & Security > Accessibility**
para conseguir ouvir/gravar cliques e repetir o mouse.

Grave uma volta:

```bash
python scripts/click_lap_recorder.py record --output records/my_route.json
```

Clique a sequencia desejada dentro da VM. Quando terminar, pressione `Esc` ou
`Cmd+Shift+Q`. O arquivo salvo guarda as coordenadas absolutas da tela da VM e o
tempo entre cliques. O gravador tambem cria um script Python ao lado do JSON,
por exemplo `records/my_route.py`, com a lista de cliques embutida.

Confira a gravacao:

```bash
python scripts/click_lap_recorder.py show --input records/my_route.json
```

Teste sem clicar:

```bash
python scripts/click_lap_recorder.py replay --input records/my_route.json --laps 3 --dry-run
```

Reproduza de verdade:

```bash
python scripts/click_lap_recorder.py replay --input records/my_route.json --laps 10
```

Ou rode o script gerado, que fica em loop ate voce parar:

```bash
python records/my_route.py
```

Opcoes uteis:

- `--laps 0`: repete em loop ate `Esc` ou `Cmd+Shift+Q`.
- `--laps 50`: repete a volta 50 vezes.
- `--speed 2`: usa metade do tempo entre cliques.
- `--speed 0.5`: usa o dobro do tempo entre cliques.
- `--spot-jitter 4`: clica ate 4 pixels ao redor do ponto gravado.
- `--time-jitter 0.08`: varia cada espera em ate 0.08 segundo para mais ou menos.
- `--inter-lap-delay 2`: espera 2 segundos entre voltas.
- `--countdown 5`: espera 5 segundos antes de iniciar gravacao/replay.

Para fazer o demo mover o cursor ate o template encontrado sem clicar, mantenha `dry_run` como `true` e altere:

```json
{
  "dry_run_move_mouse_to_match": true
}
```

Para gerar uma screenshot de diagnostico imediatamente:

```bash
python scripts/debug_once.py
```

Esse comando sempre salva uma imagem raw em `logs/debug`; se encontrar o template, salva tambem uma imagem anotada.

Para buscar apenas em uma regiao nomeada:

```bash
python scripts/debug_once.py --region inventory
```

No demo, escolha uma regiao em `config/settings.json`:

```json
{
  "template_path": "assets/templates/raw_swordfish_50.png",
  "target_region": "inventory"
}
```

Use `null` para buscar na tela inteira.

Paradas de seguranca:

- Pressione `Esc`.
- Pressione `Cmd+Shift+Q`.
- Mova o mouse para o canto superior esquerdo.
- Aguarde `max_runtime_seconds`.

## Debug

Ative debug em `config/settings.json`:

```json
{
  "debug": true,
  "debug_dir": "logs/debug"
}
```

Quando um template for encontrado, a engine salva uma imagem anotada com retangulo e score em `logs/debug`.

Screenshots de erro sao salvos em `logs/errors`.

## Troubleshooting

Se o `pip` mostrar avisos como `Cache entry deserialization failed`, limpe o cache e reinstale:

```bash
python -m pip cache purge
pip install -r requirements.txt
```

Se aparecer erro de permissao de captura ou input, feche e reabra o app de terminal depois de habilitar `Accessibility` e `Screen Recording`.

## Primeiro Script

```python
from core.mouse import MouseController
from core.screen import ScreenCapture
from core.vision import Vision

with ScreenCapture(monitor=1) as screen:
    vision = Vision(screen=screen, debug_enabled=True)
    mouse = MouseController()
    match = vision.wait_for_template("assets/templates/button.png", timeout=5)
    if match:
        mouse.click_match(match)
```

Execute seu script a partir da pasta `visual_automation` ou adicione a raiz do projeto ao `PYTHONPATH`.

## Proximos Passos

- Criar scripts especificos em `scripts/`.
- Separar templates por app ou fluxo.
- Medir scores reais e ajustar `template_threshold`.
- Definir regioes menores para cada estado.
- Expandir a state machine com acoes por estado.
- Adicionar testes unitarios para regioes, state machine e deduplicacao de matches.

## Exemplo: Cooking Swordfish

Crie a configuracao local:

```bash
cp config/cooking_swordfish.example.json config/cooking_swordfish.json
```

Adicione/calibre estes templates:

```text
assets/templates/fire.png
assets/templates/raw_swordfish_50.png
assets/templates/bank_npc.png
assets/templates/deposit_inventory.png
```

Rode primeiro em `dry_run`:

```bash
python scripts/cooking_swordfish_loop.py
```

O fluxo:

1. encontra e aponta/clica a fogueira;
2. pressiona `space` ou a tecla configurada;
3. monitora o inventario enquanto `raw_swordfish` existir;
4. quando o item sumir por algumas leituras seguidas, clica no NPC do banco;
5. clica no controle de depositar inventario;
6. clica no swordfish cru no banco;
7. reinicia o ciclo.

Mantenha regioes pequenas em `config/regions.json` para reduzir falso positivo.
