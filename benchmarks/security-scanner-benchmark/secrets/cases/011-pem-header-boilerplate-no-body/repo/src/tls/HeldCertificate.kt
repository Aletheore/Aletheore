fun privateKeyPkcs8Pem(): String = buildString {
  append("-----BEGIN PRIVATE KEY-----\n")
  encodeBase64Lines(keyPair.private.encoded.toByteString())
  append("-----END PRIVATE KEY-----\n")
}
