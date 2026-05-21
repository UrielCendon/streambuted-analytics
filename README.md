# Analytics Service

Microservicio de analitica para StreamButed. Consume eventos de reproduccion desde RabbitMQ,
persiste metricas en MongoDB y expone endpoints para dashboards de artistas, administradores
y moderacion.

## Endpoints principales

- `GET /api/v1/analytics/artists/{artistId}/summary`
- `GET /api/v1/analytics/admin/summary`
- `GET /api/v1/analytics/moderation/reports`
- `GET /api/v1/analytics/moderation/tracks/reports`
- `GET /api/v1/analytics/moderation/albums/reports`
- `GET /api/v1/analytics/moderation/users/reports`

Todos los endpoints de datos requieren JWT. El dashboard de artista acepta rol `ARTIST`
solo para su propio `artistId`, y rol `ADMIN` para consulta global. Los endpoints de
administracion y moderacion requieren rol `ADMIN`.

## Eventos consumidos

- Exchange `streaming.events`, routing key `track.playback.counted`
- Exchange `identity.events`, routing key `user.logged-in` (preparado para cuando Identity lo publique)

Los mensajes deben estar firmados con `X-Event-Signature` usando HMAC-SHA256 y
`EVENT_SIGNING_SECRET`.
