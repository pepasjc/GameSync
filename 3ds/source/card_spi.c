#include "card_spi.h"

#include <3ds.h>

// SPI commands
#define CMD_RDSR    0x05  // Read Status Register
#define CMD_READ    0x03  // Read Data
#define CMD_WREN    0x06  // Write Enable
#define CMD_WRDI    0x04  // Write Disable
#define CMD_WRITE   0x02  // Page Program / Write
#define CMD_SE      0xD8  // Sector Erase (Flash, 64KB)
#define CMD_PE      0xDB  // Page Erase (Flash, 256B)
#define CMD_JEDEC   0x9F  // JEDEC ID
#define CMD_RDID    0xAB  // Release from Deep Power-down / Read ID

// Status register bits
#define SR_WIP      0x01  // Write In Progress
#define SR_WEL      0x02  // Write Enable Latch

// Page sizes for writes
#define FLASH_PAGE_SIZE    256
#define FLASH_SECTOR_SIZE  65536
#define EEPROM_PAGE_8K     32
#define EEPROM_PAGE_64K    128
#define EEPROM_PAGE_128K   128

// Max bytes to read/write per SPI transaction
#define SPI_CHUNK_SIZE     256

static u8 s_transfer_opt;
static u64 s_wait_op;
static bool s_initialized = false;

bool card_spi_init(void) {
    if (s_initialized) return true;
    Result res = pxiDevInit();
    if (R_FAILED(res)) return false;
    s_transfer_opt = pxiDevMakeTransferOption(BAUDRATE_4MHZ, BUSMODE_1BIT);
    s_wait_op = pxiDevMakeWaitOperation(WAIT_NONE, DEASSERT_NONE, 0);
    s_initialized = true;
    return true;
}

void card_spi_exit(void) {
    if (s_initialized) {
        pxiDevExit();
        s_initialized = false;
    }
}

static PXIDEV_SPIBuffer make_buf(void *ptr, u32 size) {
    return (PXIDEV_SPIBuffer){ptr, size, s_transfer_opt, s_wait_op};
}

static PXIDEV_SPIBuffer make_empty(void) {
    return (PXIDEV_SPIBuffer){NULL, 0, s_transfer_opt, s_wait_op};
}

// Low-level: send command bytes, optionally write data, optionally read data
static Result spi_cmd(u8 *cmd, u32 cmd_len,
                      u8 *write_data, u32 write_len,
                      u8 *read_data, u32 read_len) {
    PXIDEV_SPIBuffer hdr = make_buf(cmd, cmd_len);
    PXIDEV_SPIBuffer wr = (write_data && write_len) ? make_buf(write_data, write_len) : make_empty();
    PXIDEV_SPIBuffer rd = (read_data && read_len) ? make_buf(read_data, read_len) : make_empty();
    PXIDEV_SPIBuffer empty = make_empty();
    return PXIDEV_SPIMultiWriteRead(&hdr, &wr, &rd, &empty, &empty, &empty);
}

// Read JEDEC ID (3 bytes: manufacturer, type, capacity)
static Result spi_read_jedec(u8 *id3) {
    u8 cmd = CMD_JEDEC;
    return spi_cmd(&cmd, 1, NULL, 0, id3, 3);
}

// Read status register
static Result spi_read_status(u8 *status) {
    u8 cmd = CMD_RDSR;
    return spi_cmd(&cmd, 1, NULL, 0, status, 1);
}

// Send write enable
static Result spi_write_enable(void) {
    u8 cmd = CMD_WREN;
    return spi_cmd(&cmd, 1, NULL, 0, NULL, 0);
}

// Send write disable
static Result spi_write_disable(void) {
    u8 cmd = CMD_WRDI;
    return spi_cmd(&cmd, 1, NULL, 0, NULL, 0);
}

// Wait for WIP (Write In Progress) to clear, up to timeout_ms
static bool spi_wait_wip(int timeout_ms) {
    for (int i = 0; i < timeout_ms; i++) {
        u8 sr;
        if (R_FAILED(spi_read_status(&sr))) return false;
        if (!(sr & SR_WIP)) return true;
        svcSleepThread(1000000LL); // 1ms
    }
    return false;
}

// --- Flash read/write (24-bit address) ---

static Result flash_read(u32 addr, u8 *buf, u32 len) {
    u8 cmd[4] = {CMD_READ, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF};
    return spi_cmd(cmd, 4, NULL, 0, buf, len);
}

static bool flash_write_page(u32 addr, const u8 *data, u32 len) {
    if (len > FLASH_PAGE_SIZE) len = FLASH_PAGE_SIZE;
    spi_write_enable();
    u8 cmd[4] = {CMD_WRITE, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF};
    Result res = spi_cmd(cmd, 4, (u8 *)data, len, NULL, 0);
    if (R_FAILED(res)) return false;
    return spi_wait_wip(50);
}

static bool flash_erase_sector(u32 addr) {
    spi_write_enable();
    u8 cmd[4] = {CMD_SE, (addr >> 16) & 0xFF, (addr >> 8) & 0xFF, addr & 0xFF};
    Result res = spi_cmd(cmd, 4, NULL, 0, NULL, 0);
    if (R_FAILED(res)) return false;
    return spi_wait_wip(3000); // Sector erase can take up to 3s
}

// --- EEPROM read/write (16-bit address, for 8K-64K) ---

static Result eeprom_read_2addr(u32 addr, u8 *buf, u32 len) {
    u8 cmd[3] = {CMD_READ, (addr >> 8) & 0xFF, addr & 0xFF};
    return spi_cmd(cmd, 3, NULL, 0, buf, len);
}

static bool eeprom_write_2addr(u32 addr, const u8 *data, u32 len, u32 page_size) {
    u32 offset = 0;
    while (offset < len) {
        // Align to page boundary
        u32 page_offset = (addr + offset) % page_size;
        u32 chunk = page_size - page_offset;
        if (chunk > len - offset) chunk = len - offset;

        spi_write_enable();
        u32 a = addr + offset;
        u8 cmd[3] = {CMD_WRITE, (a >> 8) & 0xFF, a & 0xFF};
        Result res = spi_cmd(cmd, 3, (u8 *)(data + offset), chunk, NULL, 0);
        if (R_FAILED(res)) return false;
        if (!spi_wait_wip(50)) return false;
        offset += chunk;
    }
    return true;
}

// --- EEPROM 128K read/write (17-bit address, bit16 in command byte bit3) ---

static Result eeprom_read_128k(u32 addr, u8 *buf, u32 len) {
    u8 cmd_byte = CMD_READ | ((addr >> 16) & 1) << 3;
    u8 cmd[3] = {cmd_byte, (addr >> 8) & 0xFF, addr & 0xFF};
    return spi_cmd(cmd, 3, NULL, 0, buf, len);
}

static bool eeprom_write_128k(u32 addr, const u8 *data, u32 len) {
    u32 offset = 0;
    while (offset < len) {
        u32 page_offset = (addr + offset) % EEPROM_PAGE_128K;
        u32 chunk = EEPROM_PAGE_128K - page_offset;
        if (chunk > len - offset) chunk = len - offset;

        spi_write_enable();
        u32 a = addr + offset;
        u8 cmd_byte = CMD_WRITE | ((a >> 16) & 1) << 3;
        u8 cmd[3] = {cmd_byte, (a >> 8) & 0xFF, a & 0xFF};
        Result res = spi_cmd(cmd, 3, (u8 *)(data + offset), chunk, NULL, 0);
        if (R_FAILED(res)) return false;
        if (!spi_wait_wip(50)) return false;
        offset += chunk;
    }
    return true;
}

// --- EEPROM 512B read/write (9-bit address, bit8 in command bit3) ---

static Result eeprom_read_512b(u32 addr, u8 *buf, u32 len) {
    u8 cmd[2] = {CMD_READ | (((addr >> 8) & 1) << 3), addr & 0xFF};
    return spi_cmd(cmd, 2, NULL, 0, buf, len);
}

static bool eeprom_write_512b(u32 addr, const u8 *data, u32 len) {
    u32 offset = 0;
    while (offset < len) {
        u32 page_offset = (addr + offset) % 16; // 16-byte pages
        u32 chunk = 16 - page_offset;
        if (chunk > len - offset) chunk = len - offset;

        spi_write_enable();
        u32 a = addr + offset;
        u8 cmd[2] = {CMD_WRITE | (((a >> 8) & 1) << 3), a & 0xFF};
        Result res = spi_cmd(cmd, 2, (u8 *)(data + offset), chunk, NULL, 0);
        if (R_FAILED(res)) return false;
        if (!spi_wait_wip(50)) return false;
        offset += chunk;
    }
    return true;
}

// --- Detection ---

CardSaveType card_spi_detect(void) {
    if (!s_initialized) return SAVE_TYPE_UNKNOWN;

    // Step 1: Try JEDEC ID for Flash detection
    u8 jedec[3] = {0};
    if (R_SUCCEEDED(spi_read_jedec(jedec))) {
        // Known manufacturers: 0x20 (ST/Numonyx), 0xC2 (Macronix),
        //                      0x62 (Sanyo), 0x1C (EON), 0xBF (SST)
        if (jedec[0] == 0x20 || jedec[0] == 0xC2 || jedec[0] == 0x62 ||
            jedec[0] == 0x1C || jedec[0] == 0xBF) {
            switch (jedec[2]) {
                case 0x10: return SAVE_TYPE_FLASH_256K; // Some 64KB chips but NDS uses as 256K
                case 0x12: return SAVE_TYPE_FLASH_256K; // 256KB (2Mbit)
                case 0x13: return SAVE_TYPE_FLASH_512K; // 512KB (4Mbit)
                case 0x14: return SAVE_TYPE_FLASH_1M;   // 1MB (8Mbit)
                case 0x17: return SAVE_TYPE_FLASH_8M;   // 8MB (64Mbit, DSi)
                default:   return SAVE_TYPE_FLASH_256K;  // Unknown capacity, assume 256K
            }
        }
    }

    // Step 2: Check if an SPI save device responds
    if (R_FAILED(spi_write_enable())) return SAVE_TYPE_UNKNOWN;

    u8 sr = 0;
    if (R_FAILED(spi_read_status(&sr))) return SAVE_TYPE_UNKNOWN;
    spi_write_disable();

    if (!(sr & SR_WEL)) return SAVE_TYPE_UNKNOWN;

    // Step 3: Distinguish address width and size using wrapping detection
    // Read 32 bytes from address 0 with 2-byte addressing
    u8 ref[32], probe[32];
    if (R_FAILED(eeprom_read_2addr(0x0000, ref, 32))) return SAVE_TYPE_UNKNOWN;

    // Check if data is all 0xFF (blank chip - can't detect wrapping)
    bool all_same = true;
    for (int i = 1; i < 32; i++) {
        if (ref[i] != ref[0]) { all_same = false; break; }
    }

    if (all_same) {
        // Can't detect wrapping on uniform data, try 1-byte addressing
        // to check for 512B EEPROM
        u8 ref1[4];
        if (R_SUCCEEDED(eeprom_read_512b(0, ref1, 4))) {
            // If 1-byte and 2-byte reads give different first bytes,
            // hard to tell. Default to most common: 64KB EEPROM
        }
        return SAVE_TYPE_EEPROM_64K;
    }

    // Check wrapping at 8KB boundary
    if (R_SUCCEEDED(eeprom_read_2addr(0x2000, probe, 32))) {
        if (memcmp(ref, probe, 32) == 0) return SAVE_TYPE_EEPROM_8K;
    }

    // Check wrapping at 32KB boundary
    if (R_SUCCEEDED(eeprom_read_2addr(0x8000, probe, 32))) {
        if (memcmp(ref, probe, 32) == 0) return SAVE_TYPE_FRAM_32K;
    }

    // No wrapping within 64KB range
    // Try to distinguish 64KB from 128KB:
    // 128KB EEPROM uses bit3 of command as A16
    // Read from upper 64K page (A16=1) addr 0x0000
    u8 upper[32];
    if (R_SUCCEEDED(eeprom_read_128k(0x10000, upper, 32))) {
        // If upper page data != lower page data, it's 128KB
        if (memcmp(ref, upper, 32) != 0) return SAVE_TYPE_EEPROM_128K;
    }

    return SAVE_TYPE_EEPROM_64K;
}

u32 card_spi_get_size(CardSaveType type) {
    switch (type) {
        case SAVE_TYPE_EEPROM_512B:  return 512;
        case SAVE_TYPE_EEPROM_8K:    return 8 * 1024;
        case SAVE_TYPE_EEPROM_64K:   return 64 * 1024;
        case SAVE_TYPE_EEPROM_128K:  return 128 * 1024;
        case SAVE_TYPE_FLASH_256K:   return 256 * 1024;
        case SAVE_TYPE_FLASH_512K:   return 512 * 1024;
        case SAVE_TYPE_FLASH_1M:     return 1024 * 1024;
        case SAVE_TYPE_FLASH_8M:     return 8 * 1024 * 1024;
        case SAVE_TYPE_FRAM_32K:     return 32 * 1024;
        default:                     return 0;
    }
}

bool card_spi_read_save(CardSaveType type, u8 *buf, u32 size) {
    if (!s_initialized || type == SAVE_TYPE_UNKNOWN || !buf || size == 0)
        return false;

    u32 save_size = card_spi_get_size(type);
    if (size < save_size) return false;

    u32 offset = 0;
    while (offset < save_size) {
        u32 chunk = SPI_CHUNK_SIZE;
        if (chunk > save_size - offset) chunk = save_size - offset;

        Result res;
        switch (type) {
            case SAVE_TYPE_EEPROM_512B:
                res = eeprom_read_512b(offset, buf + offset, chunk);
                break;
            case SAVE_TYPE_EEPROM_8K:
            case SAVE_TYPE_EEPROM_64K:
            case SAVE_TYPE_FRAM_32K:
                res = eeprom_read_2addr(offset, buf + offset, chunk);
                break;
            case SAVE_TYPE_EEPROM_128K:
                res = eeprom_read_128k(offset, buf + offset, chunk);
                break;
            case SAVE_TYPE_FLASH_256K:
            case SAVE_TYPE_FLASH_512K:
            case SAVE_TYPE_FLASH_1M:
            case SAVE_TYPE_FLASH_8M:
                res = flash_read(offset, buf + offset, chunk);
                break;
            default:
                return false;
        }
        if (R_FAILED(res)) return false;
        offset += chunk;
    }

    return true;
}

bool card_spi_write_save(CardSaveType type, const u8 *buf, u32 size) {
    if (!s_initialized || type == SAVE_TYPE_UNKNOWN || !buf || size == 0)
        return false;

    u32 save_size = card_spi_get_size(type);
    if (size > save_size) size = save_size;

    switch (type) {
        case SAVE_TYPE_FLASH_256K:
        case SAVE_TYPE_FLASH_512K:
        case SAVE_TYPE_FLASH_1M:
        case SAVE_TYPE_FLASH_8M: {
            // Flash requires sector erase before write
            for (u32 addr = 0; addr < size; addr += FLASH_SECTOR_SIZE) {
                if (!flash_erase_sector(addr)) return false;
            }
            // Write in pages
            for (u32 addr = 0; addr < size; addr += FLASH_PAGE_SIZE) {
                u32 chunk = FLASH_PAGE_SIZE;
                if (chunk > size - addr) chunk = size - addr;
                if (!flash_write_page(addr, buf + addr, chunk)) return false;
            }
            return true;
        }

        case SAVE_TYPE_EEPROM_8K:
            return eeprom_write_2addr(0, buf, size, EEPROM_PAGE_8K);

        case SAVE_TYPE_EEPROM_64K:
            return eeprom_write_2addr(0, buf, size, EEPROM_PAGE_64K);

        case SAVE_TYPE_EEPROM_128K:
            return eeprom_write_128k(0, buf, size);

        case SAVE_TYPE_EEPROM_512B:
            return eeprom_write_512b(0, buf, size);

        case SAVE_TYPE_FRAM_32K:
            // FRAM has no page limit and no erase needed, but write in chunks
            return eeprom_write_2addr(0, buf, size, size); // One big "page"

        default:
            return false;
    }
}
