# Checklist de release

## Código e contratos

- [ ] Versão segue SemVer e aparece em um único commit de release.
- [ ] Mudanças MCP incompatíveis estão identificadas no changelog.
- [ ] Configuração nova tem default seguro e exemplo canônico.
- [ ] Migração ou reconstrução de índice tem caminho de reversão.

## Segurança e privacidade

- [ ] `scripts/check_publication.py` termina com sucesso.
- [ ] Diff não contém vault, índice, log, configuração local ou caminho pessoal.
- [ ] Dependências e actions foram revisadas.
- [ ] Modelo de ameaças cobre novas fronteiras.
- [ ] Relatos privados resolvidos foram coordenados antes da divulgação.

## Validação

- [ ] Ruff check e format estão verdes.
- [ ] Typecheck do escopo publicado está verde.
- [ ] Testes sem modelos estão verdes.
- [ ] Testes lentos aplicáveis registram ambiente e resultado.
- [ ] Ambiente está sincronizado com o lockfile e wheel/sdist usam o backend
      declarado no `pyproject.toml`.
- [ ] Artefatos são inspecionados para arquivos locais indevidos.
- [ ] `scripts/check_publication.py --require-dist` valida wheel e sdist.

## Documentação

- [ ] README instala uma cópia limpa.
- [ ] Contagem de tools e resources coincide com o runtime.
- [ ] Referências de configuração coincidem com o schema Pydantic.
- [ ] Changelog separa adicionado, alterado, corrigido e segurança.
- [ ] Benchmarks publicados incluem protocolo e dados brutos.

## Entrega

- [ ] Tag aponta para o commit validado.
- [ ] Release notes derivam do changelog, sem promessas não medidas.
- [ ] Checksums dos artefatos são publicados.
- [ ] Rollback e suporte da versão estão definidos.
