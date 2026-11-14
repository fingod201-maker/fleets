package main

import (
	"log"
	"os"
)

var logger = log.New(os.Stdout, "bridge ", log.LstdFlags|log.LUTC)

func logf(format string, args ...any) {
	logger.Printf(format, args...)
}
