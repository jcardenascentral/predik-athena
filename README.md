# predik-athena — conexión a Athena para apps de Predik

Las apps consultan Athena **sin keys guardadas**. Las credenciales siempre son temporales y las entrega
AWS: el rol de la tarea en ECS, el perfil de la instancia EC2 o `aws sso login` en la laptop. Cada app
tiene su propio workgroup, con un límite de bytes por consulta, y una política de solo lectura sobre sus tablas.

```python
from predik_athena import configure_trail, make_runner, sql_source

configure_trail("SiteSelector", "1.0.0")   # nombre y versión en el comentario de cada consulta
db = make_runner()                          # Athena; o DuckDB local si MOCK_TABLES_DIR está definido

@sql_source("zonas")                        # el comentario dice qué módulo y qué función la generó
def resumen_sql():
    return "SELECT name_l1, sum(tam) AS tam FROM gi_site_selector.zonas GROUP BY 1"

rows = db.run(resumen_sql())                # [{"name_l1": "JALISCO", "tam": 120}, ...]  o QueryError
```

Instalación (fija la versión con un tag):

```text
predik-athena @ git+https://github.com/jcardenascentral/predik-athena@v1.0.0
```

## De dónde salen las credenciales

| Dónde corre | Credenciales | Qué configurar |
|---|---|---|
| ECS Express (producción) | Rol de la tarea (`<App>Task`) | Nada en la app: `task-role-arn` en `deploy.yml` |
| EC2 de staging | Perfil de la instancia | Agregar la política de la app al rol de la instancia |
| Laptop de un desarrollador | IAM Identity Center | `aws sso login --profile predik` y `AWS_PROFILE=predik` |
| Servidor fuera de AWS | Ver "Si hace falta una key" | `PREDIK_ASSUME_ROLE_ARN` |

Todas son temporales (duran de 1 a 12 h) y AWS las renueva solo. Con `PREDIK_FORBID_LONG_TERM_KEYS=true`
(en producción) la app **no arranca** con una key fija (`AKIA...`). Sin esa variable solo deja un aviso en el log.

`PREDIK_ASSUME_ROLE_ARN` asume un rol más estrecho por encima de cualquiera de las anteriores. Las
credenciales duran 1 h y se renuevan antes de vencer. No hace ninguna llamada a AWS al arrancar.

## Variables

| Variable | Default | Para qué |
|---|---|---|
| `ATHENA_WORKGROUP` | `primary` | **Uno por app**, creado con `iam/create-workgroup.sh` |
| `ATHENA_DATABASE` | `default` | Base de datos de Glue por defecto |
| `ATHENA_OUTPUT_LOCATION` | — | Solo si el workgroup no la impone (los de `create-workgroup.sh` sí) |
| `ATHENA_TIMEOUT_SECONDS` | `120` | En ECS Express, `55` (el ALB corta a los 60 s) |
| `CACHE_TTL_SECONDS` | `900` | Caché en memoria de resultados por SQL |
| `MOCK_TABLES_DIR` | — | Carpeta con `<schema>.<tabla>.csv`: corre la misma SQL en DuckDB, sin AWS |
| `PREDIK_FORBID_LONG_TERM_KEYS` | `false` | `true` en producción |
| `PREDIK_ASSUME_ROLE_ARN` | — | Rol a asumir (opcional) |
| `PREDIK_ASSUME_ROLE_EXTERNAL_ID` | — | External ID del rol, si lo exige |

## Configurar AWS para una app nueva (una vez)

```bash
# 1. Workgroup propio: salida cifrada y fija, tope de 2 GB por consulta
bash iam/create-workgroup.sh siteselector

# 2. Política mínima de solo lectura (tomar las rutas reales de Glue)
aws glue get-table --database-name gi_site_selector --name zonas --query Table.StorageDescriptor.Location
python iam/render_policy.py --app siteselector --database gi_site_selector \
    --s3 predik-geointelligence/tables/siteselector/ > policy.json

# 3. Asignarla al rol de la app (prod) y al rol de la EC2 de staging
aws iam put-role-policy --role-name SiteSelectorTask --policy-name siteselector-athena --policy-document file://policy.json
```

La política no da `glue:Create*`, `glue:Update*` ni escritura sobre los datos. Un `CREATE TABLE`, un
`DROP` o un `INSERT` fallan por permisos aunque la app tenga un bug.

## Si hace falta una key

Solo para un servidor fuera de AWS que no pueda usar IAM Roles Anywhere:

1. Crear un usuario IAM `svc-<app>-athena` cuya **única** política sea `sts:AssumeRole` sobre el rol de la app.
2. En el trust del rol, aceptar solo a ese usuario, con un `sts:ExternalId` y la IP del servidor:
   ```json
   {"Effect": "Allow", "Principal": {"AWS": "arn:aws:iam::295347072680:user/svc-siteselector-athena"},
    "Action": "sts:AssumeRole",
    "Condition": {"StringEquals": {"sts:ExternalId": "<valor aleatorio>"},
                  "IpAddress": {"aws:SourceIp": "203.0.113.10/32"}}}
   ```
3. En el servidor: la key del usuario, `PREDIK_ASSUME_ROLE_ARN` y `PREDIK_ASSUME_ROLE_EXTERNAL_ID`. La
   key sola no lee nada: solo sirve para pedir credenciales de 1 h, desde esa IP.
4. Guardarla en el gestor de secretos del hosting y rotarla cada 90 días.

## Tests

```bash
pip install -e ".[test]" && pytest -q
```

Sin AWS. Usan `botocore.Stubber` para Athena, un STS falso para la renovación de credenciales y DuckDB para el mock.
