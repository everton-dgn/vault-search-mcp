# Política de segurança

## Versões atendidas

O projeto está em fase alpha. Correções de segurança são aplicadas somente à
linha de desenvolvimento atual até que exista a primeira release estável.

## Como reportar

Use o recurso de relato privado de vulnerabilidade da plataforma que hospeda o
repositório. Se ele estiver indisponível, abra uma issue sem detalhes técnicos e
peça um canal privado aos mantenedores.

Não publique prova de conceito, conteúdo de vault, credenciais, caminhos da
máquina ou logs brutos antes da correção e da coordenação de divulgação.

Inclua no relato privado:

- versão ou commit afetado;
- pré-condições e superfície atingida;
- passos mínimos com fixtures sintéticas;
- impacto observado;
- mitigação temporária, quando conhecida.

## Limite de confiança suportado

O servidor MCP e o daemon foram desenhados para uma única pessoa, em uma
máquina controlada. O daemon deve permanecer em loopback. O projeto não fornece
autenticação, autorização multiusuário nem proteção para publicação direta na
internet.

Acesso remoto não é suportado. Essa fronteira exige TLS, autenticação, quotas e
uma revisão específica do modelo de ameaças antes de entrar no contrato público.

O conteúdo recuperado do vault é dado não confiável. Clientes devem impedir que
trechos de notas substituam instruções de sistema ou autorização do usuário.

O enriquecimento externo de frontmatter começa desativado. Quando habilitado
explicitamente, o operador assume que o conteúdo enviado ao processo externo
segue a política desse provedor.

O [modelo de ameaças](docs/security/threat-model.md) descreve ativos, fronteiras
e premissas com mais detalhes.

## Resposta esperada

Um mantenedor deve confirmar o recebimento, avaliar reprodutibilidade e combinar
uma janela de correção. Prazos variam conforme gravidade e disponibilidade. O
projeto evita prometer um SLA que ainda não consegue sustentar.
