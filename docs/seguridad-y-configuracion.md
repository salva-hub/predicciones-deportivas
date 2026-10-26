# Configuración y seguridad del repositorio (pasos para Salva)

Claude no puede cambiar la configuración del repositorio desde su sesión, así que estos pasos se hacen a mano
en github.com, una sola vez. Toman unos 10 minutos. Todo es gratis.

Abre https://github.com/salva-hub/predicciones-deportivas y entra a **Settings** (arriba a la derecha).

## 1. Protección contra filtración de claves
**Settings > Advanced Security** (en algunas cuentas se llama "Code security and analysis"):
- **Dependency graph**: Enable.
- **Dependabot alerts**: Enable.
- **Dependabot security updates**: Enable.
- **Secret Protection** / **Secret scanning**: Enable.
- **Push protection**: Enable. Bloquea cualquier subida que contenga una clave por error.
- **Private vulnerability reporting**: Enable.

## 2. Permisos de GitHub Actions
**Settings > Actions > General**:
- **Fork pull request workflows from outside collaborators**: "Require approval for all external contributors".
- **Workflow permissions**: "Read repository contents and packages permissions" (cada flujo pide lo que necesita).
- Deja desmarcado "Allow GitHub Actions to create and approve pull requests".
- Save.

## 3. Proteger la rama main
**Settings > Rules > Rulesets > New ruleset > New branch ruleset**:
- Nombre: `main`. Enforcement status: **Active**.
- Target branches: **Add target > Include default branch**.
- Marca **Restrict deletions** y **Block force pushes**. No marques "Require a pull request" (el calificador automático necesita guardar resultados en main).
- Create.

## 4. Publicar el dashboard
**Settings > Pages**:
- **Source**: "GitHub Actions".
- El dashboard quedará en https://salva-hub.github.io/predicciones-deportivas/ después de la primera corrida
  del flujo "Calificar y publicar dashboard" (Actions > Calificar y publicar dashboard > Run workflow).

## 5. Otras opciones
**Settings > General > Features**: desmarca **Wikis** y **Projects** (no se usan).

## 6. Claves de API (más adelante, cuando te las pida)
Nunca se escriben en archivos del repositorio. Van en dos lugares:
- **GitHub**: Settings > Secrets and variables > Actions > New repository secret.
- **Claude** (para los pedidos de pronósticos): en la configuración del entorno de nube del proyecto, como variable de entorno.

Nombres: `THE_ODDS_API_KEY`, `ODDSPAPI_KEY`, `API_FOOTBALL_KEY`.

## Qué es público y qué no
El repositorio es público: cualquiera puede ver los pronósticos, el registro de apuestas y el bank.
No contiene claves, contraseñas ni datos personales. Si prefieres que el registro de dinero no sea público,
se puede hacer el repositorio privado (GitHub Actions da 2,000 minutos gratis al mes; GitHub Pages requiere
repositorio público en el plan gratuito, así que el dashboard tendría que verse de otra forma).
