module github.com/ProtonMail/proton-bridge/v3

go 1.26.1

toolchain go1.26.7

replace (
	github.com/emersion/go-message => github.com/ProtonMail/go-message v0.13.1-0.20240919135104-3bc88e6a9423
	github.com/emersion/go-smtp => github.com/ProtonMail/go-smtp v0.0.0-20231109081432-2b3d50599865
	github.com/go-ctap/winhello => github.com/ProtonMail/winhello v0.0.0-20260223131736-d2c4f2d06287
	github.com/go-resty/resty/v2 => github.com/ProtonMail/resty/v2 v2.0.0-20250929142426-e3dc6308c80b
	github.com/keybase/go-keychain => github.com/ProtonMail/go-keychain v0.0.0-20250929142014-ea8548dff768
	github.com/keys-pub/go-libfido2 => github.com/ProtonMail/go-libfido2 v0.0.0-20250916110427-df894d6d07a1
)
