# Port forwarder for `ec2-sim forward`: publishes host localhost:PORT -> instance:PORT.
FROM alpine:3.20
RUN apk add --no-cache socat
ENTRYPOINT ["socat"]
