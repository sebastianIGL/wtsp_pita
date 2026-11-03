/* Tailwind 3.4.17 (la misma versión que servía el CDN). Sitio público + login, restablecer contraseña y Mi Camino.
   Configuración por defecto, igual que la del CDN en esas páginas. */
module.exports = {
  content: [
    './frontend/home.html',
    './frontend/como-funciona.html',
    './frontend/subsidios.html',
    './frontend/viviendas.html',
    './frontend/proyecto.html',
    './frontend/legal_base.html',
    './frontend/terminos.html',
    './frontend/privacidad.html',
    './frontend/cookies.html',
    './frontend/blog.html',
    './frontend/login.html',
    './frontend/reset-password.html',
    './frontend/mi_camino.html',
    './frontend/partials/*.html',
  ],
  theme: { extend: {} },
  plugins: [],
};
