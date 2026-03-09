#ifndef CARD_SPI_H
#define CARD_SPI_H

#include "common.h"

// NDS cartridge save chip types
typedef enum {
    SAVE_TYPE_UNKNOWN = 0,
    SAVE_TYPE_EEPROM_512B,   // 512 bytes, 9-bit address (bit8 in command)
    SAVE_TYPE_EEPROM_8K,     // 8KB, 16-bit address
    SAVE_TYPE_EEPROM_64K,    // 64KB, 16-bit address
    SAVE_TYPE_EEPROM_128K,   // 128KB, 17-bit address (bit16 in command)
    SAVE_TYPE_FLASH_256K,    // 256KB, 24-bit address
    SAVE_TYPE_FLASH_512K,    // 512KB, 24-bit address
    SAVE_TYPE_FLASH_1M,      // 1MB, 24-bit address
    SAVE_TYPE_FLASH_8M,      // 8MB, 24-bit address (DSi)
    SAVE_TYPE_FRAM_32K,      // 32KB, 16-bit address, instant writes
} CardSaveType;

// Initialize the PXIDEV service for SPI access
bool card_spi_init(void);
void card_spi_exit(void);

// Detect the save chip type on the inserted NDS cartridge
CardSaveType card_spi_detect(void);

// Get save size in bytes for a given type
u32 card_spi_get_size(CardSaveType type);

// Read entire save from NDS cartridge into buffer
// buf must be at least card_spi_get_size(type) bytes
bool card_spi_read_save(CardSaveType type, u8 *buf, u32 size);

// Write entire save to NDS cartridge from buffer
bool card_spi_write_save(CardSaveType type, const u8 *buf, u32 size);

#endif // CARD_SPI_H
