/* Tailwind 3.4.17 (la misma versión que servía el CDN). Sitio público + login, restablecer contraseña y Mi Camino.
   Configuración por defecto, igual que la del CDN en esas páginas. */
module.exports = {
  content: [
    './frontend/home.html',
    './frontend/como-funciona.html',
    './frontend/subsidios.html',
    './frontend/viviendas.html',
    './frontend/blog.html',
    './frontend/login.html',
    './frontend/reset-password.html',
    './frontend/opcion_c.html',
    './frontend/partials/*.html',
  ],
  theme: { extend: {} },
  plugins: [],
};
